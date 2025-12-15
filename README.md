# Chat gRPC Distribuído com Algoritmo de Eleição Bully

Este projeto contém um sistema de chat distribuído com:
- Servidor gRPC (`chat_server.py`) com suporte a **múltiplas réplicas**
- Cliente CLI (`chat_client.py`) com **reconexão automática**
- Algoritmo de Eleição Bully para tolerância a falhas
- Relógio de Lamport para ordenação parcial das mensagens

## Arquitetura do Sistema
Arquitetura híbrida: comunicação P2P entre servidores para eleição e coordenação, e cliente-servidor para o chat entre clientes e o líder.

### Cliente-Servidor com Replicação 

Este sistema utiliza uma arquitetura **Cliente-Servidor centralizada com replicação de servidores**. 

### P2P entre Servidores
Os servidores formam um **cluster P2P** para coordenar a eleição do líder e monitorar a saúde do líder via heartbeat. 


**Como funciona:**
- Existem múltiplos servidores que formam um **cluster**
- Apenas **um servidor (o líder)** atende os clientes por vez
- Os outros servidores ficam em **standby** como backups
- Clientes **sempre se comunicam com o servidor líder** - nunca diretamente entre si
- Se o líder falha, o **Algoritmo de Eleição Bully** elege um novo líder automaticamente
- Os clientes reconectam ao novo líder de forma transparente

**O que é `--peers`:**
O parâmetro `--peers` define a lista de outros servidores do cluster que este servidor conhece. É usado exclusivamente para comunicação servidor-servidor:
1. **Heartbeat** - detectar se o líder está vivo (a cada 2 segundos)
2. **Eleição** - enviar mensagens ELECTION e COORDINATOR do algoritmo Bully
3. **Sincronização** - permitir que servidores troquem informações de estado

Tabela feita por IA para ilustrar a arquitetura do sistema:
```
┌───────────────────────────────────────────────────────────────────────────┐
│              CAMADA DE SERVIDORES (Server-to-Server (P2P))                │
│                                                                           │
│   Server 1 ◄────── peers ──────► Server 2 ◄───── peers ─────► Server 3    │
│   (backup)        (eleição/       (backup)      (eleição/      (LÍDER)    │
│                   heartbeat)                    heartbeat)                │
└───────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ gRPC (Cliente-Servidor)
                                      │ Apenas o LÍDER atende clientes
                                      ▼
        ┌────────────────────────────────────────────────────────────────┐
        │                     CAMADA DE CLIENTES                         │
        │                                                                │
        │            Cliente 1        Cliente 2        Cliente 3         │
        │                                                                │
        └────────────────────────────────────────────────────────────────┘
```

| Aspecto | Descrição |
|---------|-----------|
| **Arquitetura** | Cliente-Servidor com múltiplas réplicas de servidor |
| **Comunicação Cliente↔Servidor** | gRPC tradicional (request-response e streaming) |
| **Comunicação Servidor↔Servidor** | gRPC para eleição e heartbeat (os "peers") |
| **Líder** | Apenas 1 servidor ativo atende clientes por vez |
| **Backups** | Outros servidores ficam em standby, prontos para assumir |
| **Tolerância a falhas** | Se líder cai, eleição Bully escolhe novo líder |

## Funcionalidades

### Chat Básico
- Vários clientes conectam-se ao servidor líder
- Mensagens são broadcast para todos os clientes conectados
- Ordenação parcial de mensagens via Relógio Lógico de Lamport

### Algoritmo de Eleição Bully
- Múltiplos servidores podem ser executados em cluster
- Cada servidor tem um **ID único** (maior ID = maior prioridade)
- O servidor com **maior ID ativo** é eleito como **líder**
- Quando o líder falha:
  1. Outros servidores detectam via **heartbeat**
  2. Inicia-se uma **eleição**
  3. Servidor envia ELECTION para todos com ID maior
  4. Se receber OK, aguarda COORDINATOR
  5. Se não receber OK, declara-se líder e envia COORDINATOR para todos
- Clientes reconectam automaticamente ao novo líder

### Heartbeat
- Servidores enviam pings periódicos para o líder
- Se o líder não responde, inicia-se a eleição
- Intervalo de heartbeat configurável (padrão: 2 segundos)
- Garante alta disponibilidade do serviço de chat
- Minimiza o tempo de inatividade percebido pelos clientes
- Permite uma transição suave entre líderes
- Assegura que os clientes continuem conectados sem interrupções significativas       
- Heartbeat é apenas para DETECÇÃO DE FALHAS (ping/pong).
- NÃO incrementa o Relógio de Lamport porque:
  - Lamport é para ordenar EVENTOS DE COMUNICAÇÃO (mensagens de chat)
  - Heartbeat não é uma mensagem de chat, é apenas verificação de vida
  - Se incluíssemos, os timestamps ficariam "poluídos" com valores altos

## Requisitos

- Python 3.9+
- `grpcio`, `grpcio-tools`, `protobuf`

## Instalação (Linux)

```bash
# Criar ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Regenerar arquivos gRPC (se necessário, passo não obrigatório)
python -m grpc_tools.protoc -I./proto --python_out=./proto --grpc_python_out=./proto ./proto/chat_server.proto
# Após regenerar (caso necessário), edite proto/chat_server_pb2_grpc.py e mude:
"import chat_server_pb2" para "from . import chat_server_pb2"
```

### Instalação (Windows)
```powershell
# Criar ambiente virtual
py -3 -m venv venv

# Ativar (PowerShell)
.\venv\Scripts\Activate.ps1
# ou (CMD)
venv\Scripts\activate.bat

# Instalar dependências
py -3 -m pip install -r requirements.txt

# Regenerar arquivos gRPC (se necessário, passo não obrigatório)
py -3 -m grpc_tools.protoc -I./proto --python_out=./proto --grpc_python_out=./proto ./proto/chat_server.proto
# Após regenerar (caso necessário), edite proto/chat_server_pb2_grpc.py e mude:
"import chat_server_pb2" para "from . import chat_server_pb2"
```

## Executando com Múltiplos Servidores (Cluster)

### Terminal 1 - Servidor 1 (ID=1, porta 50051)
```bash
# Linux
source venv/bin/activate
python chat_server.py --id 1 --port 50051 --peers "2:localhost:50052,3:localhost:50053"
```
```powershell
# PowerShell
.\venv\Scripts\Activate.ps1
# ou CMD
venv\Scripts\activate.bat

python chat_server.py --id 1 --port 50051 --peers "2:localhost:50052,3:localhost:50053"
```
### Terminal 2 - Servidor 2 (ID=2, porta 50052)
```bash
# Linux
source venv/bin/activate
python chat_server.py --id 2 --port 50052 --peers "1:localhost:50051,3:localhost:50053"
```
```powershell
# PowerShell
.\venv\Scripts\Activate.ps1
# ou CMD
venv\Scripts\activate.bat

python chat_server.py --id 2 --port 50052 --peers "1:localhost:50051,3:localhost:50053"
```

### Terminal 3 - Servidor 3 (ID=3, porta 50053)
```bash
# Linux
source venv/bin/activate
python chat_server.py --id 3 --port 50053 --peers "1:localhost:50051,2:localhost:50052"
```
```powershell
# PowerShell
.\venv\Scripts\Activate.ps1
# ou CMD
venv\Scripts\activate.bat

python chat_server.py --id 3 --port 50053 --peers "1:localhost:50051,2:localhost:50052"
```

**Resultado:** O Servidor 3 será eleito líder (maior ID).

## Testando o Chat com Múltiplos Clientes

Após iniciar os 3 servidores, abra mais terminais para os clientes:

### Terminal 4 - Cliente 1
```bash
# Linux
source venv/bin/activate
python chat_client.py --servers "localhost:50051,localhost:50052,localhost:50053"
```
```powershell
# PowerShell
.\venv\Scripts\Activate.ps1
# ou CMD
venv\Scripts\activate.bat

python chat_client.py --servers "localhost:50051,localhost:50052,localhost:50053"
```

### Terminal 5 - Cliente 2
```bash
# Linux
source venv/bin/activate
python chat_client.py --servers "localhost:50051,localhost:50052,localhost:50053"
```
```powershell
# PowerShell
.\venv\Scripts\Activate.ps1
# ou CMD
venv\Scripts\activate.bat

python chat_client.py --servers "localhost:50051,localhost:50052,localhost:50053"
```

### Terminal 6 - Cliente 3
```bash
# Linux
source venv/bin/activate
python chat_client.py --servers "localhost:50051,localhost:50052,localhost:50053"
```
```powershell
# PowerShell
.\venv\Scripts\Activate.ps1
# ou CMD
venv\Scripts\activate.bat

python chat_client.py --servers "localhost:50051,localhost:50052,localhost:50053"
```

### Testando o Chat:
1. No **Cliente 1**, digite: `Olá, sou o cliente 1!` e pressione Enter
2. Observe a mensagem aparecer nos **Clientes 2 e 3**
3. No **Cliente 2**, responda: `Oi cliente 1, aqui é o cliente 2!`
4. Observe a mensagem chegar nos **Clientes 1 e 3**

**Exemplo de saída no Cliente 2:**
```
[rec][ts=5] de 1: Olá, sou o cliente 1!
```

## Testando a Eleição 

1. Inicie os 3 servidores (Servidor 3 será o líder)
2. Conecte 2 ou mais clientes
3. Envie algumas mensagens para confirmar que funciona
4. **Mate o Servidor 3** (Ctrl+C no Terminal 3)
5. Observe nos logs dos servidores 1 e 2:
   ```
   [ELEIÇÃO] Servidor 2: Líder 3 não respondeu ao heartbeat!
   [ELEIÇÃO] Servidor 2: Iniciando eleição...
   [ELEIÇÃO] Servidor 2: Declarando-me líder!
   ```
6. **Servidor 2** assume como novo líder
7. Clientes reconectam automaticamente
8. Continue enviando mensagens - tudo funciona normalmente!

## Argumentos do Servidor

| Argumento | Descrição | Exemplo |
|-----------|-----------|---------|
| `--id` | ID único do servidor (obrigatório) | `--id 1` |
| `--port` | Porta do servidor | `--port 50051` |
| `--peers` | Lista de peers: "id:host:port,..." | `--peers "2:localhost:50052"` |

## Argumentos do Cliente

| Argumento | Descrição | Exemplo |
|-----------|-----------|---------|
| `--servers` | Lista de servidores | `--servers "localhost:50051,localhost:50052"` |


## Protocolo de Eleição (Bully Algorithm)

```
Servidor 1 detecta que líder (3) falhou
    │
    ▼
Servidor 1 envia ELECTION para 2 e 3
    │
    ├─► Servidor 3: não responde (falhou)
    │
    └─► Servidor 2: responde OK (ID maior que 1)
            │
            ▼
        Servidor 2 inicia sua própria eleição
        Envia ELECTION para 3
            │
            ▼
        Servidor 3 não responde
            │
            ▼
        Servidor 2 se declara LÍDER
        Envia COORDINATOR para todos
            │
            ▼
        Servidor 1 recebe COORDINATOR
        Atualiza líder para 2
```
## Testes de Desempenho

Na pasta `/experiments` estão os arquivos referentes aos testes de 
desempenho, assim como as métricas obtidas.