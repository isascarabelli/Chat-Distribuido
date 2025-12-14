# Avaliação de Desempenho – Chat gRPC Distribuído

VERSÃO ATUALIZADA – SEM FLAGS, SEM SCRIPT SHELL, SEM DOWNTIME COMO MÉTRICA PRINCIPAL

Este documento descreve o processo de **avaliação de desempenho** do sistema *Chat gRPC Distribuído com Algoritmo de Eleição Bully*, desenvolvido como trabalho prático final da disciplina **Computação Distribuída**, do curso de **Engenharia de Computação**.

O objetivo deste material é:

- Documentar a metodologia de testes adotada;
- Garantir a reprodutibilidade dos experimentos;
- Explicitar as métricas coletadas e suas interpretações;
- Delimitar o escopo e as limitações da avaliação sintética realizada.

---

## 1. Objetivo da Avaliação de Desempenho

A avaliação de desempenho visa caracterizar o comportamento do sistema distribuído sob cargas **sintéticas controladas**, analisando:

- Latência percebida pelos clientes;
- Vazão (*throughput*) do sistema;
- Comportamento sob concorrência crescente;
- Tolerância a falhas do servidor líder, observada como degradação temporária do serviço.

Os testes são executados **em ambiente local**, com foco na validação funcional e no desempenho lógico do sistema distribuído. Não há utilização de rede externa real.

---

## 2. Arquitetura Considerada nos Testes

Os testes utilizam exatamente a arquitetura implementada no projeto, sem simplificações ou reimplementações:

- Arquitetura Cliente–Servidor com replicação de servidores;
- Apenas um servidor líder atende clientes em um dado momento;
- Servidores em *standby* executam o algoritmo de eleição Bully;
- Comunicação cliente–servidor via gRPC;
- Comunicação servidor–servidor via gRPC (heartbeat e eleição);
- Reconexão automática dos clientes após falha do líder.

A avaliação **não reimplementa nenhuma lógica distribuída**.  
O script de testes reutiliza o `ChatClient` original do projeto.

---

## 3. Metodologia de Testes

### 3.1 Abordagem

Os testes são conduzidos do ponto de vista do cliente, pois representam a experiência observável pelo usuário final.

Para cada cenário:

- Um cluster local fixo com **3 servidores** é inicializado automaticamente;
- **N** clientes concorrentes são criados;
- Cada cliente envia **M** mensagens;
- Um intervalo fixo entre envios é respeitado;
- Métricas são coletadas durante toda a execução.

No cenário com falha:

- O processo do servidor líder é encerrado propositalmente durante a execução;
- O cluster executa o algoritmo de eleição Bully;
- Os clientes realizam reconexões automáticas;
- O impacto é observado como degradação temporária (falhas de envio e variações de latência e vazão).

Cada cenário é executado de forma **isolada**, garantindo ausência de interferência entre execuções consecutivas.

---

## 4. Métricas Coletadas

### 4.1 Latência de envio (RPC)

Tempo de execução da chamada `client.send()`, incluindo:

- Custos de serialização e transmissão via gRPC;
- Processamento no servidor líder;
- Custos adicionais em períodos de reconexão.

### 4.2 Vazão (*Throughput*)

Calculada como o total de mensagens enviadas dividido pelo tempo total do cenário, expressa em mensagens por segundo.

### 4.3 Variabilidade

Medida por meio do desvio padrão da latência.

### 4.4 Falhas temporárias de envio

Quantidade de exceções observadas durante chamadas de envio. Essa métrica representa períodos de degradação transitória do serviço, especialmente durante a troca de liderança e reconexões.

> Observação: embora o código mantenha um campo de *downtime*, nesta avaliação não foi observada indisponibilidade total contínua do serviço. A recuperação ocorreu predominantemente como degradação transitória, sendo a métrica de falhas temporárias de envio a mais representativa do impacto do failover.

---

## 5. Script de Avaliação

A avaliação é executada exclusivamente pelo script:

```
performance_analysis.py
```

Características principais:

- Não aceita parâmetros de entrada;
- Inicializa e encerra automaticamente o cluster de servidores;
- Executa uma bateria fixa de cenários;
- Gera arquivos CSV por cenário e um CSV consolidado;
- Imprime uma tabela-resumo no terminal ao final da execução.

---

## 6. Execução dos Testes

Execução da bateria completa de testes:

```bash
python performance_analysis.py
```

---

## 7. Cenários de Teste Executados

| Cenário       | Clientes | Mensagens | Intervalo (s) | Falha do líder |
|--------------|----------|-----------|---------------|----------------|
| Base         | 2        | 20        | 0.10          | Não            |
| Carga Normal | 5        | 100       | 0.05          | Não            |
| Failover     | 5        | 100       | 0.05          | Sim            |

---

## 8. Arquivos Gerados

Cada execução gera um diretório único em:

```
experiments/results/<execution_id>/
```

Com:

- Um subdiretório por cenário;
- `resultados.csv` por cenário;
- `resultados_consolidados.csv`.

---

## 9. Limitações Conhecidas

- Avaliação executada em ambiente local e sintético;
- Não representa latência de rede real;
- O algoritmo Bully pode apresentar instabilidade sob cargas extremas se timeouts de heartbeat forem agressivos.
