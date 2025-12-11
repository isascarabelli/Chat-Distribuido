import grpc
from concurrent import futures
import threading
import queue
import time
import logging
import argparse

import chat_server_pb2 as pb
import chat_server_pb2_grpc as pb_grpc

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


# Algoritmo de Eleição Bullying entre os servidores 
# Funcionamento:
# 1. Quando um processo detecta que o líder falhou, inicia uma eleição
# 2. Envia ELECTION para todos os processos com ID maior
# 3. Se receber OK de algum, espera pelo COORDINATOR
# 4. Se não receber OK, declara-se líder e envia COORDINATOR para todos
# 5. O processo com maior ID sempre vence
# 6. O id é passado como argumento na inicialização do servidor
# 7. Cada servidor conhece os peers (id, address) dos outros servidores
# 8. Usa heartbeat para detectar falha do líder (a cada 2 segundos envia um ping)
class BullyElection:
    def __init__(self, server_id: int, peers: list, lamport_clock: LamportClock, on_leader_change=None):
        self.server_id = server_id
        self.peers = peers  # Lista de (id, address) dos outros servidores
        self.lamport_clock = lamport_clock
        self.leader_id = None
        self.is_leader = False
        self._lock = threading.Lock()
        self._election_in_progress = False
        self._on_leader_change = on_leader_change
        
        # Timeouts
        self.election_timeout = 3.0  # segundos para esperar resposta OK
        self.coordinator_timeout = 5.0  # segundos para esperar COORDINATOR
        
    def get_leader(self):
        with self._lock:
            return self.leader_id
    
    def am_i_leader(self):
        with self._lock:
            return self.is_leader
    
    def set_leader(self, leader_id: int):
        with self._lock:
            old_leader = self.leader_id
            self.leader_id = leader_id
            self.is_leader = (leader_id == self.server_id)
            if old_leader != leader_id:
                logging.info(f"[ELEIÇÃO] Novo líder: Servidor {leader_id}")
                if self._on_leader_change:
                    self._on_leader_change(leader_id)
    
    # Inicia o processo de eleição 
    def start_election(self):
        # Evita múltiplas eleições simultâneas
        with self._lock:
            if self._election_in_progress:
                logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Eleição já em progresso")
                return
            self._election_in_progress = True
        
        logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Iniciando eleição...")
        ts = self.lamport_clock.incrementaRelogio()
        
        # Encontra servidores com ID maior
        higher_peers = [(pid, addr) for pid, addr in self.peers if pid > self.server_id]
        
        if not higher_peers:
            # Eu tenho o maior ID, então me declaro líder
            logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Nenhum peer com ID maior. Declarando-me líder!")
            self._declare_me_leader()
            return
        
        # Envia ELECTION para todos com ID maior, caso não seja o ID maior
        received_ok = False
        for peer_id, peer_addr in higher_peers:
            try:
                channel = grpc.insecure_channel(peer_addr)
                stub = pb_grpc.ElectionModuleStub(channel)
                request = pb.ElectionRequest(
                    candidate_id=self.server_id,
                    lamport_timestamp=ts
                )
                response = stub.Election(request, timeout=self.election_timeout)
                if response.ok:
                    logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Recebeu OK de {response.responder_id}")
                    self.lamport_clock.updateRelogio(response.lamport_timestamp)
                    received_ok = True
                channel.close()
            except grpc.RpcError as e:
                logging.debug(f"[ELEIÇÃO] Servidor {peer_id} não respondeu: {e.code()}")
        
        if received_ok:
            # Espera pelo COORDINATOR
            logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Aguardando COORDINATOR...")
            time.sleep(self.coordinator_timeout)
            # Se não recebeu coordinator, inicia nova eleição
            with self._lock:
                if self.leader_id is None:
                    self._election_in_progress = False
                    threading.Thread(target=self.start_election, daemon=True).start()
        else:
            # Nenhum respondeu, eu sou o líder (precaução caso falhe durante a eleição)
            logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Nenhuma resposta OK. Declarando-me líder!")
            self._declare_me_leader()
        
        with self._lock:
            self._election_in_progress = False
    
    # Declara-se líder e envia COORDINATOR para todos
    def _declare_me_leader(self):
        self.set_leader(self.server_id)
        ts = self.lamport_clock.incrementaRelogio()
        
        # Envia COORDINATOR para todos os peers
        for peer_id, peer_addr in self.peers:
            try:
                channel = grpc.insecure_channel(peer_addr)
                stub = pb_grpc.ElectionModuleStub(channel)
                request = pb.CoordinatorRequest(
                    leader_id=self.server_id,
                    lamport_timestamp=ts
                )
                stub.Coordinator(request, timeout=2.0)
                channel.close()
                logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Enviou COORDINATOR para {peer_id}")
            except grpc.RpcError:
                logging.debug(f"[ELEIÇÃO] Falha ao enviar COORDINATOR para {peer_id}")
        
        with self._lock:
            self._election_in_progress = False
    
    # Responde a uma mensagem ELECTION
    def handle_election(self, candidate_id: int, timestamp: int) -> tuple:
        self.lamport_clock.updateRelogio(timestamp)
        
        if self.server_id > candidate_id:
            # Respondo OK e inicio minha própria eleição, pois tenho ID maior
            logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Recebeu ELECTION de {candidate_id}, respondendo OK")
            threading.Thread(target=self.start_election, daemon=True).start()
            return True, self.server_id
        return False, self.server_id
    
    # Recebe anúncio de novo líder
    def handle_coordinator(self, leader_id: int, timestamp: int):
        self.lamport_clock.updateRelogio(timestamp)
        logging.info(f"[ELEIÇÃO] Servidor {self.server_id}: Recebeu COORDINATOR - Novo líder é {leader_id}")
        self.set_leader(leader_id)


# Classe do serviço de chat distribuído com eleição (servidor)
class ChatService(pb_grpc.ClientModuleServicer, pb_grpc.ServerModuleServicer, pb_grpc.ElectionModuleServicer):
    def __init__(self, server_id: int, port: int, peers: list):
        self._server_id = server_id
        self._port = port
        self._address = f"localhost:{port}"
        self._subscribers = {}
        self._lock = threading.Lock()
        self._next_client_id = 1
        self._lamport_clock = LamportClock()
        self._message_history = []  # Para sincronização
        
        # Instancia o algoritmo de eleição
        self._election = BullyElection(
            server_id=server_id,
            peers=peers,
            lamport_clock=self._lamport_clock,
            on_leader_change=self._on_leader_change
        )
        
        # Thread de heartbeat para detectar falha do líder
        self._heartbeat_interval = 2.0 # ping a cada 2 segundos
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._running = True

    # Inicia threads de background após o servidor estar rodando
    def start_background_tasks(self):
        self._heartbeat_thread.start()
        # Inicia uma eleição ao entrar no cluster
        time.sleep(1)  # Espera servidor inicializar
        threading.Thread(target=self._election.start_election, daemon=True).start()

    # Callback quando o líder muda
    def _on_leader_change(self, new_leader_id: int):
        logging.info(f"[SERVER {self._server_id}] Líder mudou para: {new_leader_id}")
    
    def _heartbeat_loop(self):
        """
        Loop que verifica se o líder está vivo.
        
        O Heartbeat é um mecanismo de DETECÇÃO DE FALHAS:
        - A cada 2 segundos, servidores backup enviam "ping" ao líder
        - Se o líder não responder, significa que falhou
        - Quando detecta falha, inicia uma nova ELEIÇÃO
        
        IMPORTANTE: Heartbeat NÃO incrementa o Relógio de Lamport porque:
        - Lamport é para ordenar EVENTOS DE COMUNICAÇÃO (mensagens de chat)
        - Heartbeat não é uma mensagem de chat, é apenas verificação de vida
        - Se incluíssemos, os timestamps ficariam "poluídos" com valores altos
          (ex: ts=253 ao invés de ts=3 para a terceira mensagem)
        """
        while self._running:
            time.sleep(self._heartbeat_interval)
            
            leader_id = self._election.get_leader()
            if leader_id is None or leader_id == self._server_id:
                continue
            
            # Encontra endereço do líder
            leader_addr = None
            for pid, addr in self._election.peers:
                if pid == leader_id:
                    leader_addr = addr
                    break
            
            if leader_addr is None:
                continue
            
            # Envia heartbeat para o líder (apenas ping/pong, sem Lamport)
            try:
                channel = grpc.insecure_channel(leader_addr)
                stub = pb_grpc.ElectionModuleStub(channel)
                request = pb.HeartbeatRequest(
                    server_id=self._server_id,
                    lamport_timestamp=0  # Não usado - heartbeat não é evento de comunicação
                )
                response = stub.Heartbeat(request, timeout=2.0)
                channel.close()
            except grpc.RpcError:
                logging.warning(f"[SERVER {self._server_id}] Líder {leader_id} não respondeu ao heartbeat!")
                # Líder falhou, inicia eleição
                threading.Thread(target=self._election.start_election, daemon=True).start()

    # Heartbeat para detectar falha do líder (ping/pong)
    # Não incrementa o Relógio de Lamport
    def Heartbeat(self, request, context):
        return pb.HeartbeatResponse(
            alive=True,
            leader_id=self._election.get_leader() or 0,
            lamport_timestamp=0  # Não altera o relógio de Lamport
        )
    
    # Métodos do Algoritmo de Eleição Bully
    # Recebe mensagem ELECTION do Algoritmo de Bully
    def Election(self, request, context):
        ok, responder_id = self._election.handle_election(
            request.candidate_id, 
            request.lamport_timestamp
        )
        ts = self._lamport_clock.get_time()
        return pb.ElectionResponse(
            ok=ok,
            responder_id=responder_id,
            lamport_timestamp=ts
        )
    
    # Recebe mensagem COORDINATOR do Algoritmo de Bully com o anuncio de novo líder
    def Coordinator(self, request, context):
        self._election.handle_coordinator(
            request.leader_id,
            request.lamport_timestamp
        )
        ts = self._lamport_clock.get_time()
        return pb.CoordinatorResponse(
            acknowledged=True,
            lamport_timestamp=ts
        )
    
    # Sincroniza estado (mensagens) com outro servidor (novo líder)
    def SyncState(self, request, context):
        ts = self._lamport_clock.updateRelogio(request.last_timestamp)
        with self._lock:
            # Retorna mensagens após o timestamp solicitado
            msgs = [m for m in self._message_history if m.lamport_timestamp > request.last_timestamp]
        return pb.SyncResponse(messages=msgs, lamport_timestamp=ts)
    
    # Lida quando o cliente pergunta quem é o líder
    def GetLeader(self, request, context):
        leader_id = self._election.get_leader()
        leader_addr = ""
        
        if leader_id == self._server_id:
            leader_addr = self._address
        else:
            for pid, addr in self._election.peers:
                if pid == leader_id:
                    leader_addr = addr
                    break
        
        return pb.LeaderInfo(
            leader_id=leader_id or 0,
            leader_address=leader_addr,
            is_leader_known=(leader_id is not None)
        )

   # Conecta um novo cliente ao líder
    def SubscribeToServerEvents(self, request, context):
        # Verifica se este servidor é o líder
        if not self._election.am_i_leader():
            # Redireciona para o líder
            leader_info = self.GetLeader(request, context)
            if leader_info.is_leader_known:
                redirect_msg = pb.TextMessage(
                    client_id_from=0,
                    content=f"REDIRECT:{leader_info.leader_address}",
                    lamport_timestamp=self._lamport_clock.get_time(),
                )
                yield redirect_msg
                return
        
        q = queue.Queue()
        with self._lock:
            client_id = self._next_client_id
            self._next_client_id += 1
            self._subscribers[client_id] = q
            ts = self._lamport_clock.updateRelogio(0)

        print(f"[SERVER {self._server_id}] Cliente {client_id} conectado (ts={ts})")
        assigned_msg = pb.TextMessage(
            client_id_from=0,
            content=f"ID Atribuido:{client_id}",
            lamport_timestamp=ts,
        )
        yield assigned_msg

        try:
            while context.is_active():
                try:
                    msg = q.get(timeout=1.0)
                except queue.Empty:
                    continue
                yield msg
        finally:
            with self._lock:
                if client_id in self._subscribers:
                    del self._subscribers[client_id]
            print(f"[SERVER {self._server_id}] Cliente {client_id} desconectado")

    # Recebe mensagem do cliente (caso seja o líder)
    def SendMessageToServer(self, request, context):
        print(f"[SERVER {self._server_id}] Mensagem recebida de cliente {request.client_id_from} (ts_recebido={request.lamport_timestamp}): '{request.content}'")
        
        # Armazena no histórico
        with self._lock:
            self._message_history.append(request)
            # Mantém apenas as últimas 100 mensagens
            if len(self._message_history) > 100:
                self._message_history = self._message_history[-100:]
        
        return self.PushMessageToClients(request, context)

    # Broadcast da mensagem para todos os clientes conectados
    def PushMessageToClients(self, request, context):
        with self._lock:
            new_ts = self._lamport_clock.updateRelogio(request.lamport_timestamp)
        
        to_broadcast = pb.TextMessage(
            client_id_from=request.client_id_from,
            content=request.content,
            lamport_timestamp=new_ts,
        )

        with self._lock:
            subscribers = list(self._subscribers.items())

        target_nodes = [cid for cid, _ in subscribers if cid != request.client_id_from]
        print(f"[SERVER {self._server_id}] Encaminhando mensagem (ts={to_broadcast.lamport_timestamp}) para {len(target_nodes)} cliente(s): {target_nodes}")

        for cid, q in subscribers:
            if cid == request.client_id_from:
                continue
            try:
                q.put_nowait(to_broadcast)
            except Exception:
                logging.exception("Falha em enviar mensagem para cliente %s", cid)

        return pb.StatusResponse(success=True, client_id=request.client_id_from, message="Pushed")

    # Para o servidor (Ctrl + C)
    def stop(self):
        self._running = False

# Faz o parse da string de peers no formato "id1:host1:port1,id2:host2:port2"
# Retorna lista de (id, address) conhecidos, excluindo o próprio servidor
def parse_peers(peers_str: str, my_id: int) -> list:
    if not peers_str:
        return []
    
    peers = []
    for peer in peers_str.split(','):
        parts = peer.strip().split(':')
        if len(parts) == 3:
            peer_id = int(parts[0])
            peer_addr = f"{parts[1]}:{parts[2]}"
            if peer_id != my_id:
                peers.append((peer_id, peer_addr))
    return peers

# Inicializa o servidor 
def serve(server_id: int, port: int, peers: list):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    servicer = ChatService(server_id=server_id, port=port, peers=peers)
    
    pb_grpc.add_ClientModuleServicer_to_server(servicer, server)
    pb_grpc.add_ServerModuleServicer_to_server(servicer, server)
    pb_grpc.add_ElectionModuleServicer_to_server(servicer, server)
    
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logging.info(f"Chat Server {server_id} instanciado na porta {port}")
    logging.info(f"Peers conhecidos: {peers}")
    
    # Inicia tasks de background (heartbeat, eleição inicial)
    servicer.start_background_tasks()
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logging.info("Parando server...")
        servicer.stop()
        server.stop(0)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
    parser = argparse.ArgumentParser(description='Chat Server com Algoritmo de Eleição Bully')
    parser.add_argument('--id', type=int, required=True, help='ID único do servidor (usado na eleição)')
    parser.add_argument('--port', type=int, default=50051, help='Porta do servidor')
    parser.add_argument('--peers', type=str, default='', 
                        help='Lista de peers no formato "id1:host1:port1,id2:host2:port2"')
    args = parser.parse_args()
    
    peers = parse_peers(args.peers, args.id)
    serve(args.id, args.port, peers)
