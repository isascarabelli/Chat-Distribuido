#!/bin/bash
# ============================================================
# Script de Execução de Experimentos
# Chat gRPC Distribuído com Eleição Bully
#
# Disciplina: Computação Distribuída
# Curso: Engenharia de Computação
#
# Este script executa automaticamente todos os
# cenários de avaliação de desempenho descritos
# no README de experimentos.
# ============================================================

set -e

echo "=============================================="
echo " Avaliação de Desempenho – Chat gRPC Distribuído"
echo "=============================================="

# Caminho do script de análise
PERF_SCRIPT="./performance_analysis.py"

# Verificação do ambiente virtual
if [ ! -d "../venv" ]; then
  echo "[ERRO] Ambiente virtual não encontrado."
  echo "Crie o venv conforme descrito no README."
  exit 1
fi

echo "[INFO] Ativando ambiente virtual..."
source ../venv/bin/activate

# Verificação do Python
PY_VERSION=$(python3 --version)
echo "[INFO] Python utilizado: $PY_VERSION"

# ============================================================
# Inicialização do cluster
# ============================================================

echo
echo "[INFO] Inicializando cluster de servidores..."
python3 $PERF_SCRIPT --start-cluster &
CLUSTER_PID=$!

# Aguarda tempo para estabilização do cluster
sleep 5

# ============================================================
# Função auxiliar para executar cenários
# ============================================================

run_scenario () {
  echo
  echo "----------------------------------------------"
  echo "Executando cenário: $1"
  echo "----------------------------------------------"
  shift
  python3 $PERF_SCRIPT "$@"
}

# ============================================================
# Cenários SEM failover
# ============================================================

echo
echo "=============================================="
echo " Cenários sem Failover"
echo "=============================================="

# Cenário 8.1 – Base
run_scenario "Base (1 cliente)" \
  --clients 1 --messages 20 --interval 0.1

# Cenário 8.2 – Uso Realista
run_scenario "Uso Realista (5 clientes)" \
  --clients 5 --messages 100 --interval 0.05

# Cenário 8.3 – Escalabilidade
run_scenario "Escalabilidade (10 clientes)" \
  --clients 10 --messages 100 --interval 0.02

# ============================================================
# Cenários COM failover
# ============================================================

echo
echo "=============================================="
echo " Cenários com Failover"
echo "=============================================="

# Cenário 8.4 – Failover
run_scenario "Failover (5 clientes)" \
  --clients 5 --messages 120 --interval 0.05 --failover

# Cenário 8.5 – Failover sob carga
run_scenario "Failover sob carga (10 clientes)" \
  --clients 10 --messages 100 --interval 0.02 --failover

# ============================================================
# Finalização
# ============================================================

echo
echo "[INFO] Finalizando cluster..."
kill $CLUSTER_PID 2>/dev/null || true

deactivate

echo
echo "=============================================="
echo " Todos os experimentos foram concluídos"
echo " Resultados disponíveis no diretório 'resultados/'"
echo "=============================================="
