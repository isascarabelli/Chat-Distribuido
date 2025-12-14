# Avaliação de Desempenho – Chat gRPC Distribuído

Este documento descreve o processo de **avaliação de desempenho** do sistema *Chat gRPC Distribuído com Algoritmo de Eleição Bully*, desenvolvido como trabalho prático final da disciplina **Computação Distribuída**, do curso de **Engenharia de Computação**.

O objetivo deste material é permitir:
- Compreender a metodologia de testes adotada
- Executar os testes de forma reproduzível
- Interpretar corretamente as métricas coletadas
- Entender as limitações e o escopo dos testes sintéticos realizados

---

##  1. Objetivo da Avaliação de Desempenho

A avaliação de desempenho visa analisar o comportamento do sistema distribuído sob diferentes cargas sintéticas, considerando:

- Latência percebida pelos clientes
- Vazão (throughput) do sistema
- Escalabilidade com aumento do número de clientes
- Tolerância a falhas do servidor líder
- Downtime percebido durante a eleição de um novo líder

Os testes são **sintéticos**, executados em ambiente local, e focam na **validação funcional e de desempenho lógico** do sistema, sem a utilização de rede real externa.

---

## 2. Arquitetura Considerada nos Testes

Os testes consideram exatamente a arquitetura implementada no projeto:

- Arquitetura Cliente–Servidor com replicação de servidores
- Apenas um servidor líder atende clientes por vez
- Servidores backup participam do algoritmo de eleição Bully
- Comunicação cliente-servidor via gRPC
- Comunicação servidor-servidor via gRPC (heartbeat e eleição)
- Reconexão automática dos clientes após falha do líder

A avaliação **não reimplementa** nenhuma lógica distribuída.  
O script de testes reutiliza o `ChatClient` original do projeto.

---

## 3. Metodologia de Testes

### 3.1 Abordagem

Os testes são realizados do ponto de vista do cliente, pois este representa a experiência real de uso do sistema.

Cada cenário de teste executa:
- N clientes concorrentes
- Cada cliente envia M mensagens
- Intervalo fixo entre envios
- Medição de métricas durante toda a execução

Em cenários com falha:
- O servidor líder é encerrado propositalmente
- O tempo de indisponibilidade percebido é medido
- A continuidade do serviço é verificada

---

## 4. Métricas Coletadas

As seguintes métricas são coletadas automaticamente:

### 4.1 Latência
- Tempo de execução da chamada `client.send()`
- Inclui custo de:
  - Comunicação gRPC
  - Processamento no líder
  - Reconexão automática (quando ocorre)

### 4.2 Vazão (Throughput)
- Calculada como:
  
  total de mensagens enviadas / tempo total do cenário

### 4.3 Variabilidade
- Desvio padrão da latência

### 4.4 Tolerância a Falhas
- Número de falhas temporárias de envio
- Downtime percebido durante a eleição de um novo líder

---

## 5. Requisitos do Ambiente

### 5.1 Sistema Operacional
- Linux

### 5.2 Versão do Python
- Python 3.9 ou superior

### 5.3 Dependências

As dependências são as mesmas do projeto principal.

## 6. Script de Avaliação

O script principal de testes é:

```
performance_analysis.py
```

Este script:
- Pode inicializar o cluster automaticamente
- Executa cenários de carga sintética
- Executa testes com e sem failover
- Gera histórico organizado por execução
- Exporta resultados em CSV e relatório Markdown

---

## 7. Execução dos Testes

### 7.1 Inicializar o Cluster Automaticamente

```bash
python performance_analysis.py --start-cluster
```

Neste modo, o script sobe três servidores:
- Servidor 1 – porta 50051
- Servidor 2 – porta 50052
- Servidor 3 – porta 50053

---

### 7.2 Executar Testes com Cluster Já Ativo

```bash
python performance_analysis.py   --servers "127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053"
```

---

## 8. Cenários de Teste Predefinidos

Os cenários abaixo foram utilizados para avaliação sintética completa do 
sistema.

### 8.1 Cenário Base (Validação Funcional)

```bash
python performance_analysis.py --clients 1 --messages 20 --interval 0.1
```

Objetivo:
- Validar funcionamento correto
- Medir latência mínima
- Estabelecer baseline

---

### 8.2 Cenário de Uso Realista (Principal)

```bash
python performance_analysis.py --clients 5 --messages 100 --interval 0.05
```

Objetivo:
- Avaliar desempenho típico
- Medir latência média e vazão estável

---

### 8.3 Cenário de Escalabilidade

```bash
python performance_analysis.py --clients 10 --messages 100 --interval 0.02
```

Objetivo:
- Avaliar impacto do aumento de concorrência
- Identificar gargalo no servidor líder

---

### 8.4 Cenário de Failover (Obrigatório)

```bash
python performance_analysis.py --clients 5 --messages 120 --interval 0.05 
--failover
```

Objetivo:
- Validar algoritmo de eleição Bully
- Medir downtime percebido
- Verificar reconexão automática

---

### 8.5 Cenário de Failover sob Carga (Opcional)

```bash
python performance_analysis.py --clients 10 --messages 100 --interval 0.02 
--failover
```

Objetivo:
- Avaliar robustez do sistema durante falha em alta carga

---

## 9. Arquivos Gerados

Cada execução gera um diretório único, preservando histórico:

```
resultados/
└── chat_perf_YYYYMMDD_HHMMSS_xxxxxx/
    ├── resultados.csv
    └── relatorio.md
```

### 9.1 resultados.csv
- Contém métricas estruturadas por execução
- Pode ser utilizado para gráficos e análises estatísticas

### 9.2 relatorio.md
- Relatório automático resumido
- Pode ser convertido para PDF ou usado como base para o relatório final em LaTeX

---

## 10. Considerações Finais

Os testes sintéticos realizados são suficientes para:

- Validar o funcionamento do sistema distribuído
- Analisar desempenho sob diferentes cargas
- Demonstrar tolerância a falhas do líder
- Justificar o uso do algoritmo de eleição Bully

A metodologia adotada prioriza clareza, reprodutibilidade e coerência com a arquitetura implementada, atendendo plenamente aos objetivos acadêmicos da disciplina.
