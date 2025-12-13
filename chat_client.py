import grpc
import threading
import time
import sys
import argparse
import logging

from proto import chat_server_pb2 as pb
from proto import chat_server_pb2_grpc as pb_grpc

# Classe para o Relógio de Lamport
class LamportClock:
    def __init__(self):
        self._timestamp = 0
        self._lock = threading.Lock()

    def get_time(self):
        with self._lock:
            return self._timestamp

    def incrementaRelogio(self):
        with self._lock:
            self._timestamp += 1
            return self._timestamp

    def updateRelogio(self, received_timestamp):
        with self._lock:
            self._timestamp = max(self._timestamp, received_timestamp) + 1
            return self._timestamp

# Classe do Cliente do Chat distribuído
# servers: lista de endereços de servidores no formato ["host:port", ...]
# O cliente tentará conectar ao líder automaticamente.
class ChatClient:
    def __init__(self, servers: list):
        self._servers = servers  # Lista de todos os servidores conhecidos
        self._current_server = None
        self._channel = None
        self._stub = None
        self._lamport_clock = LamportClock()
        self._client_id = None
        self._running = True
        self._connected = False
        self._reconnect_lock = threading.Lock()
        
        # Conecta ao primeiro servidor disponível (que redirecionará ao líder)
        self._connect_to_leader()
        
        # Inicia thread de recebimento
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    # Tenta conectar ao líder do cluster
    def _connect_to_leader(self):
        with self._reconnect_lock:
            for server_addr in self._servers:
                try:
                    logging.info(f"Tentando conectar a {server_addr}...")
                    channel = grpc.insecure_channel(server_addr)
                    stub = pb_grpc.ClientModuleStub(channel)
                    
                    # Pergunta quem é o líder
                    leader_info = stub.GetLeader(pb.Empty(), timeout=3.0)
                    
                    if leader_info.is_leader_known and leader_info.leader_address:
                        leader_addr = leader_info.leader_address
                        logging.info(f"Líder identificado: {leader_addr}")
                        
                        # Conecta diretamente ao líder
                        if leader_addr != server_addr:
                            channel.close()
                            channel = grpc.insecure_channel(leader_addr)
                            stub = pb_grpc.ClientModuleStub(channel)
                        
                        self._channel = channel
                        self._stub = stub
                        self._current_server = leader_addr
                        self._connected = True
                        logging.info(f"Conectado ao líder: {leader_addr}")
                        return True
                    else:
                        # Servidor não conhece líder ainda, tenta conectar mesmo assim
                        self._channel = channel
                        self._stub = stub
                        self._current_server = server_addr
                        self._connected = True
                        logging.info(f"Conectado a {server_addr} (líder desconhecido)")
                        return True
                        
                except grpc.RpcError as e:
                    logging.warning(f"Falha ao conectar a {server_addr}: {e.code()}")
                    continue
            
            logging.error("Não foi possível conectar a nenhum servidor!")
            self._connected = False
            return False
    # Tenta reconectar ao cluster
    def _reconnect(self):
        logging.info("Tentando reconectar...")
        self._connected = False
        
        # Espera um pouco antes de tentar reconectar
        time.sleep(2)
        
        # Máximo de tentativas para reconexão são 5
        max_retries = 5
        for attempt in range(max_retries):
            if self._connect_to_leader():
                # Reinicia thread de recebimento
                if not self._recv_thread.is_alive():
                    self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
                    self._recv_thread.start()
                return True
            logging.warning(f"Tentativa {attempt + 1}/{max_retries} falhou. Aguardando...")
            time.sleep(2 ** attempt)  # Backoff exponencial
        
        return False

    # Loop de recebimento de mensagens
    def _recv_loop(self):
        while self._running:
            if not self._connected:
                time.sleep(1)
                continue
                
            try:
                for msg in self._stub.SubscribeToServerEvents(pb.Empty()):
                    if not self._running:
                        break
                    
                    # Verifica se é um redirecionamento
                    if msg.content and msg.content.startswith('REDIRECT:'):
                        new_addr = msg.content.split(':', 1)[1]
                        logging.info(f"Redirecionando para líder: {new_addr}")
                        self._current_server = new_addr
                        if self._channel:
                            self._channel.close()
                        self._channel = grpc.insecure_channel(new_addr)
                        self._stub = pb_grpc.ClientModuleStub(self._channel)
                        continue

                    # Server envia uma mensagem que inicia com "ID Atribuido"
                    if msg.content and msg.content.startswith('ID Atribuido:'):
                        try:
                            self._client_id = int(msg.content.split(':', 1)[1])
                            logging.info('ID Atribuido: %s', self._client_id)
                        except Exception:
                            logging.exception('Falha em atribuir ID')
                        continue

                    # Atualiza Lamport e printa a mensagem recebida
                    self._lamport_clock.updateRelogio(msg.lamport_timestamp)
                    print(f"[rec][ts={msg.lamport_timestamp}] Mensagem vinda de {msg.client_id_from}: {msg.content}")

            except grpc.RpcError as e:
                if self._running:
                    logging.warning(f'Conexão perdida: {e.code()}')
                    self._connected = False
                    self._reconnect()
    
    # Envia mensagem para o servidor
    def send(self, content: str):
        if not self._connected:
            logging.warning("Não conectado ao servidor!")
            return None
            
        ts = self._lamport_clock.incrementaRelogio()
        client_id = self._client_id if self._client_id is not None else 0
        msg = pb.TextMessage(client_id_from=client_id, content=content, lamport_timestamp=ts)
        
        try:
            resp = self._stub.SendMessageToServer(msg)
            return resp
        except grpc.RpcError as e:
            logging.warning(f'Falha no envio: {e.code()}')
            # Tenta reconectar e reenviar
            if self._reconnect():
                try:
                    resp = self._stub.SendMessageToServer(msg)
                    return resp
                except grpc.RpcError:
                    logging.exception('Falha no reenvio após reconexão')
            raise

    # Fecha conexão (Ctrl + C)
    def close(self):
        self._running = False
        try:
            if self._channel:
                self._channel.close()
        except Exception:
            pass

# Faz o parse da string de servidores no formato "host1:port1,host2:port2"
# Server deafault: localhost:50051
def parse_servers(servers_str: str) -> list:
    if not servers_str:
        return ['localhost:50051']
    return [s.strip() for s in servers_str.split(',')]


def main():
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
    parser = argparse.ArgumentParser(description='Chat Cliente com suporte a múltiplos servidores')
    parser.add_argument('--servers', type=str, default='localhost:50051',
                        help='Lista de servidores no formato "host1:port1,host2:port2"')

    args = parser.parse_args()

    servers = parse_servers(args.servers)
    
    print(f'Servidores conhecidos: {servers}')
    
    client = ChatClient(servers=servers)
    print('Conectado ao cluster de servidores')
    print('Digite sua mensagem e pressione enter. Ctrl+C para sair.')

    # Loop principal de envio de mensagens
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            text = line.strip()
            if text == '':
                continue
            client.send(text)
    except KeyboardInterrupt:
        print('\nSaindo...')
    finally:
        client.close()


if __name__ == '__main__':
    main()
