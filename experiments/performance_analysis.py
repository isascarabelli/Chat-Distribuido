#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Avaliação de Desempenho – Chat gRPC Distribuído
Curso: Engenharia de Computação
Disciplina: Computação Distribuída
Grupo:
    Fernando Vieira da Rocha Valentim
    Amanda Veiga de Moura
    Isabela Ferreira Scarabelli
    Pedro Henrique de Almeida Santos
    Rafael Felipe Silva Pereira

Avaliação completa de:
- Latência
- Vazão
- Escalabilidade
- Tolerância a falhas
- Downtime percebido
- Eleição Bully

O teste reutiliza o ChatClient real do projeto.
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import argparse
import csv
import signal
import statistics
import subprocess
import time
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional

import grpc

import chat_server_pb2 as pb
import chat_server_pb2_grpc as pb_grpc
from chat_client import ChatClient, parse_servers


# ======================================================
# Utilidades
# ======================================================

def exec_id():
    return f"chat_perf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def ipv4_only(s: str) -> str:
    return s.replace("localhost", "127.0.0.1")


def mkdir(p: str):
    os.makedirs(p, exist_ok=True)


# ======================================================
# Cluster
# ======================================================

@dataclass
class ServerProc:
    server_id: int
    proc: subprocess.Popen


def start_cluster(python_exec, server_script) -> List[ServerProc]:
    cfg = [
        (1, 50051, "2:127.0.0.1:50052,3:127.0.0.1:50053"),
        (2, 50052, "1:127.0.0.1:50051,3:127.0.0.1:50053"),
        (3, 50053, "1:127.0.0.1:50051,2:127.0.0.1:50052"),
    ]
    procs = []
    for sid, port, peers in cfg:
        p = subprocess.Popen([
            python_exec, server_script,
            "--id", str(sid),
            "--port", str(port),
            "--peers", peers
        ])
        procs.append(ServerProc(sid, p))
    return procs


def stop_cluster(procs):
    for sp in procs:
        if sp.proc.poll() is None:
            sp.proc.send_signal(signal.SIGINT)
    time.sleep(2)
    for sp in procs:
        if sp.proc.poll() is None:
            sp.proc.kill()


# ======================================================
# Métricas compartilhadas
# ======================================================

latencias = []
lat_lock = threading.Lock()

falhas_send = []
falha_lock = threading.Lock()

downtime_inicio = None
downtime_fim = None
downtime_lock = threading.Lock()


# ======================================================
# Cliente worker
# ======================================================

class ClientWorker(threading.Thread):
    def __init__(self, cid, servers, msgs, intervalo, start_barrier):
        super().__init__(daemon=True)
        self.cid = cid
        self.servers = servers
        self.msgs = msgs
        self.intervalo = intervalo
        self.start_barrier = start_barrier

    def run(self):
        global downtime_inicio, downtime_fim

        client = ChatClient(self.servers)

        while not client._connected:
            time.sleep(0.05)

        self.start_barrier.wait()

        for i in range(self.msgs):
            try:
                t0 = time.time()
                client.send(f"[teste] cliente {self.cid} msg {i}")
                t1 = time.time()
                with lat_lock:
                    latencias.append(t1 - t0)
            except Exception:
                with falha_lock:
                    falhas_send.append(time.time())
                with downtime_lock:
                    if downtime_inicio is None:
                        downtime_inicio = time.time()
            time.sleep(self.intervalo)

        client.close()


# ======================================================
# Failover
# ======================================================

def kill_leader_after(delay_s, servers, cluster_procs):
    global downtime_inicio, downtime_fim

    time.sleep(delay_s)

    leader_id = None
    for addr in servers:
        try:
            ch = grpc.insecure_channel(addr)
            stub = pb_grpc.ClientModuleStub(ch)
            info = stub.GetLeader(pb.Empty(), timeout=1)
            if info.is_leader_known:
                leader_id = info.leader_id
                ch.close()
                break
        except Exception:
            pass

    if leader_id is None:
        return

    for sp in cluster_procs:
        if sp.server_id == leader_id:
            sp.proc.send_signal(signal.SIGINT)
            break

    # aguarda novo líder
    while True:
        try:
            for addr in servers:
                ch = grpc.insecure_channel(addr)
                stub = pb_grpc.ClientModuleStub(ch)
                info = stub.GetLeader(pb.Empty(), timeout=1)
                if info.is_leader_known and info.leader_id != leader_id:
                    with downtime_lock:
                        if downtime_inicio is not None:
                            downtime_fim = time.time()
                    return
        except Exception:
            pass
        time.sleep(0.2)


# ======================================================
# Execução de cenário
# ======================================================

def run_scenario(exec_id, servers_str, clientes, msgs, intervalo, failover, cluster):
    global latencias, falhas_send, downtime_inicio, downtime_fim
    latencias = []
    falhas_send = []
    downtime_inicio = None
    downtime_fim = None

    servers = parse_servers(servers_str)
    barrier = threading.Barrier(clientes)

    workers = [
        ClientWorker(i, servers, msgs, intervalo, barrier)
        for i in range(clientes)
    ]

    if failover:
        threading.Thread(
            target=kill_leader_after,
            args=(3, servers, cluster),
            daemon=True
        ).start()

    t0 = time.time()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    t1 = time.time()

    downtime = 0.0
    if downtime_inicio and downtime_fim:
        downtime = downtime_fim - downtime_inicio

    return {
        "exec_id": exec_id,
        "cenario": f"{clientes}x{msgs}x{intervalo}",
        "clientes": clientes,
        "mensagens": msgs,
        "intervalo": intervalo,
        "tempo_total": t1 - t0,
        "total_msgs": clientes * msgs,
        "vazao": (clientes * msgs) / (t1 - t0),
        "lat_media": statistics.mean(latencias),
        "lat_min": min(latencias),
        "lat_max": max(latencias),
        "lat_desvio": statistics.stdev(latencias) if len(latencias) > 1 else 0,
        "falhas_send": len(falhas_send),
        "downtime": downtime
    }


# ======================================================
# Main
# ======================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--servers", default="127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053")
    parser.add_argument("--clients", type=int, default=5)
    parser.add_argument("--messages", type=int, default=50)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--start-cluster", action="store_true")
    parser.add_argument("--failover", action="store_true")
    parser.add_argument("--out-dir", default="resultados")
    parser.add_argument("--server-script", default="chat_server.py")
    args = parser.parse_args()

    eid = exec_id()
    out_dir = os.path.join(args.out_dir, eid)
    mkdir(out_dir)

    servers_str = ipv4_only(args.servers)

    cluster = None
    if args.start_cluster:
        cluster = start_cluster(sys.executable, args.server_script)
        time.sleep(5)

    try:
        r = run_scenario(
            eid, servers_str,
            args.clients, args.messages, args.interval,
            args.failover, cluster
        )

        with open(os.path.join(out_dir, "resultados.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=r.keys())
            w.writeheader()
            w.writerow(r)

        print("\n===== RESULTADOS =====")
        for k, v in r.items():
            print(f"{k}: {v}")

    finally:
        if cluster:
            stop_cluster(cluster)


if __name__ == "__main__":
    main()
