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
PYTHON_EXEC = sys.executable
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
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict

import grpc

from proto import chat_server_pb2 as pb
from proto import chat_server_pb2_grpc as pb_grpc
from chat_client import ChatClient, parse_servers

SERVER_SCRIPT = "../chat_server.py"
OUTPUT_DIR_ROOT = "results"


# ======================================================
# Definição fixa dos cenários a serem testados:
# ======================================================

SCENARIOS = [
    {"name": "baseline", "clients": 2, "messages": 20, "interval": 0.1,
     "failover": False},
    {"name": "principal", "clients": 5, "messages": 100, "interval": 0.05,
     "failover": False},
    #{"name": "stress_10c", "clients": 10, "messages": 100, "interval": 0.05,
     #"failover": False},
    {"name": "failover_5c", "clients": 5, "messages": 120, "interval": 0.05,
     "failover": True},
   # {"name": "failover_10c", "clients": 10, "messages": 100, "interval": 0.05,
    # "failover": True},
]

# ======================================================
# Utilidades
# ======================================================

def exec_id() -> str:
    return f"chat_perf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def mkdir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def safe_mean(xs: List[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def safe_min(xs: List[float]) -> float:
    return min(xs) if xs else 0.0


def safe_max(xs: List[float]) -> float:
    return max(xs) if xs else 0.0


def safe_stdev(xs: List[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


# ======================================================
# Cluster
# ======================================================

@dataclass
class ServerProc:
    server_id: int
    proc: subprocess.Popen


def start_cluster():
    """Sobe um cluster fixo 3 nós (IDs 1..3) nas portas 50051..50053."""
    cfg = [
        (1, 50051, "2:127.0.0.1:50052,3:127.0.0.1:50053"),
        (2, 50052, "1:127.0.0.1:50051,3:127.0.0.1:50053"),
        (3, 50053, "1:127.0.0.1:50051,2:127.0.0.1:50052"),
    ]
    procs: List[ServerProc] = []
    servers = []
    for sid, port, peers in cfg:
        p = subprocess.Popen(
            [
                PYTHON_EXEC, SERVER_SCRIPT,
                "--id", str(sid),
                "--port", str(port),
                "--peers", peers
            ],
            stdout=None,
            stderr=None
        )
        procs.append(ServerProc(sid, p))
        servers.append(f"127.0.0.1:{port}")
        time.sleep(0.5)
    return procs, servers


def stop_cluster(procs: List[ServerProc]):
    for sp in procs:
        if sp.proc.poll() is None:
            try:
                sp.proc.send_signal(signal.SIGINT)
            except Exception:
                pass
    time.sleep(2)
    for sp in procs:
        if sp.proc.poll() is None:
            try:
                sp.proc.kill()
            except Exception:
                pass


# ======================================================
# Métricas
# ======================================================

@dataclass
class ScenarioMetrics:
    """Container de métricas com acesso protegido por lock externo."""
    latencias: List[float]
    falhas_send: int = 0
    downtime_inicio: Optional[float] = None
    downtime_fim: Optional[float] = None


# ======================================================
# Cliente worker
# ======================================================

class ClientWorker(threading.Thread):
    def __init__(
        self,
        cid: int,
        servers: List[str],
        msgs: int,
        intervalo: float,
        barrier: threading.Barrier,
        metrics: ScenarioMetrics,
        metrics_lock: threading.Lock,
        stop_event: threading.Event,
        connect_timeout_s: float = 5.0,
    ):
        super().__init__(daemon=True)
        self.cid = cid
        self.servers = servers
        self.msgs = msgs
        self.intervalo = intervalo
        self.barrier = barrier
        self.metrics = metrics
        self.metrics_lock = metrics_lock
        self.stop_event = stop_event
        self.connect_timeout_s = connect_timeout_s

    def run(self):
        client = ChatClient(self.servers)

        start = time.time()
        while not getattr(client, "_connected", False):
            if self.stop_event.is_set():
                client.close()
                return
            if time.time() - start > self.connect_timeout_s:
                client.close()
                return
            time.sleep(0.05)

        # Sincroniza início do envio entre os clientes
        try:
            self.barrier.wait(timeout=20)
        except threading.BrokenBarrierError:
            client.close()
            return

        for i in range(self.msgs):
            if self.stop_event.is_set():
                break

            try:
                t0 = time.time()
                client.send(f"[teste] cliente {self.cid} msg {i}")
                t1 = time.time()
                with self.metrics_lock:
                    self.metrics.latencias.append(t1 - t0)
            except Exception:
                now = time.time()
                with self.metrics_lock:
                    self.metrics.falhas_send += 1
                    if self.metrics.downtime_inicio is None:
                        self.metrics.downtime_inicio = now
                time.sleep(min(self.intervalo, 0.2))
                continue

            time.sleep(self.intervalo)

        client.close()


# ======================================================
# Failover
# ======================================================

def _get_leader_id(servers: List[str], timeout_s: float = 1.0) -> Optional[int]:
    for addr in servers:
        ch = None
        try:
            ch = grpc.insecure_channel(addr)
            stub = pb_grpc.ClientModuleStub(ch)
            info = stub.GetLeader(pb.Empty(), timeout=timeout_s)
            if info.is_leader_known:
                return int(info.leader_id)
        except Exception:
            pass
        finally:
            try:
                if ch is not None:
                    ch.close()
            except Exception:
                pass
    return None


def kill_leader_after(
    delay_s: float,
    servers: List[str],
    cluster: Optional[List[ServerProc]],
    metrics: ScenarioMetrics,
    metrics_lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    if cluster is None:
        return

    time.sleep(delay_s)
    if stop_event.is_set():
        return

    leader_id = _get_leader_id(servers, timeout_s=1.0)
    if leader_id is None:
        return

    # Derruba o líder atual
    for sp in cluster:
        if sp.server_id == leader_id and sp.proc.poll() is None:
            try:
                sp.proc.send_signal(signal.SIGINT)
            except Exception:
                pass
            break

    # Aguarda novo líder para marcar fim do downtime (se já houve início)
    while not stop_event.is_set():
        new_leader = _get_leader_id(servers, timeout_s=1.0)
        if new_leader is not None and new_leader != leader_id:
            with metrics_lock:
                if metrics.downtime_inicio is not None and metrics.downtime_fim is None:
                    metrics.downtime_fim = time.time()
            return
        time.sleep(0.2)


# ======================================================
# Execução de cenário
# ======================================================

def run_scenario(execute_id: str, clientes: int, msgs: int, intervalo: float,
                 failover: bool):
    cluster, servers = start_cluster()
    time.sleep(2)

    metrics = ScenarioMetrics(latencias=[])
    metrics_lock = threading.Lock()
    stop_event = threading.Event()

    barrier = threading.Barrier(parties=clientes)

    workers = [
        ClientWorker(
            cid=i,
            servers=servers,
            msgs=msgs,
            intervalo=intervalo,
            barrier=barrier,
            metrics=metrics,
            metrics_lock=metrics_lock,
            stop_event=stop_event,
        )
        for i in range(clientes)
    ]

    failover_thread = None
    if failover:
        failover_thread = threading.Thread(
            target=kill_leader_after,
            args=(3, servers, cluster, metrics, metrics_lock, stop_event),
            daemon=True,
        )
        failover_thread.start()

    t0 = time.time()
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=60)

    stop_event.set()
    for w in workers:
        if w.is_alive():
            w.join(timeout=2)

    if failover_thread:
        failover_thread.join(timeout=10)

    t1 = time.time()

    with metrics_lock:
        lat = list(metrics.latencias)
        falhas = int(metrics.falhas_send)
        dt_ini = metrics.downtime_inicio
        dt_fim = metrics.downtime_fim

    downtime = 0.0
    if dt_ini is not None and dt_fim is not None and dt_fim >= dt_ini:
        downtime = dt_fim - dt_ini

    total_msgs = clientes * msgs
    tempo_total = max(t1 - t0, 1e-9)

    if cluster:
        stop_cluster(cluster)

    return {
        "execute_id": execute_id,
        "clientes": clientes,
        "mensagens": msgs,
        "intervalo": intervalo,
        "tempo_total": tempo_total,
        "total_msgs": total_msgs,
        "vazao": total_msgs / tempo_total,
        "lat_media": safe_mean(lat),
        "lat_min": safe_min(lat),
        "lat_max": safe_max(lat),
        "lat_desvio": safe_stdev(lat),
        "falhas_send": falhas
    }

def print_summary_table(rows):
    headers = [
        "Cenário",
        "Lat. média (ms)",
        "Desvio (ms)",
        "Vazão (msgs/s)",
        "Falhas (envio)"
    ]

    line = "-" * 78
    fmt = "{:<20} {:>15} {:>12} {:>15} {:>12}"

    print("\nTabela 1. Métricas de desempenho consolidadas por cenário.")
    print(line)
    print(fmt.format(*headers))
    print(line)

    for r in rows:
        print(fmt.format(
            r["cenario"],
            f"{r['lat_media']*1000:.2f}",
            f"{r['lat_desvio']*1000:.2f}",
            f"{r['vazao']:.2f}",
            f"{r['falhas_send']}"
        ))

    print(line)



# ======================================================
# Main
# ======================================================

def main():

    eid = exec_id()
    out_dir = os.path.join(OUTPUT_DIR_ROOT, eid)
    mkdir(out_dir)

    consolidated: List[Dict] = []
    print(f"\nExecução ID: {eid}")
    print("=" * 50)

    for s in SCENARIOS:
        print(f"\n>>> Cenário: {s['name']}")

        result = run_scenario(
            execute_id=eid,
            clientes=s["clients"],
            msgs=s["messages"],
            intervalo=s["interval"],
            failover=s["failover"]
        )

        result["cenario"] = s["name"]
        consolidated.append(result)
        scenario_dir = os.path.join(out_dir, s["name"])
        mkdir(scenario_dir)
        with open(os.path.join(scenario_dir, "resultados.csv"), "w",
                  newline="") as f:
            w = csv.DictWriter(f, fieldnames=result.keys())
            w.writeheader()
            w.writerow(result)

        time.sleep(2)

    # CSV consolidado
    consolidated_csv = os.path.join(out_dir, "resultados_consolidados.csv")
    with open(consolidated_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=consolidated[0].keys())
        w.writeheader()
        w.writerows(consolidated)

    print("\nExecução finalizada.")
    print(f"Resultados em: {out_dir}")
    print_summary_table(consolidated)

if __name__ == "__main__":
    main()
