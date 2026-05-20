# TSP Safety Layer — Motor de Decisão TSP e Safety Layer

## Objetivo

O TSP Safety Layer transforma os pedidos C-ITS aceites pela RSU em decisões semafóricas auditáveis e seguras. O objetivo é passar de um fluxo apenas comunicacional:

```text
OBU -> SREM-like -> RSU -> SSEM-like
```

para um fluxo operacional controlado:

```text
OBU -> RSU -> Motor TSP -> Safety Layer -> Atuador TraCI/dry-run
```

## Componentes implementados

### 1. Motor de decisão TSP

Localização:

```text
src/pps57_tsp/engine.py
```

O motor avalia cada pedido `SREM_like` aceite pela RSU e calcula uma decisão baseada em:

- atraso do autocarro;
- desvio de headway;
- proximidade à interseção;
- nível de prioridade;
- ETA até à stopline;
- estado semafórico atual `SPATEM_like`/`SignalState`.

Ações possíveis:

- `no_action`: o verde atual já é suficiente ou não há intervenção útil;
- `green_extension`: prolongamento controlado da fase verde atual;
- `early_green`: antecipação do verde por truncagem da fase conflitante;
- `reevaluate_next_cycle`: reavaliar no ciclo seguinte;
- `reject`: pedido sem prioridade suficiente para atuação.

### 2. Safety Layer

Localização:

```text
src/pps57_tsp/safety.py
```

A safety layer valida a decisão antes de qualquer atuação. Regras aplicadas:

- limite máximo de extensão de verde;
- limite de verde total;
- respeito por verde mínimo antes de truncagem de fase;
- bloqueio se a fase atual estiver em amarelo;
- cooldown depois de intervenção prioritária;
- limite de intervenções consecutivas por TLS;
- ausência de manipulação direta do estado vermelho/amarelo/verde.

### 3. Atuadores

Localização:

```text
src/pps57_tsp/actuator.py
```

Foram implementados dois atuadores:

- `DryRunTSPActuator`: não usa TraCI; regista o que seria aplicado.
- `TraciTSPActuator`: aplica comandos seguros via TraCI quando o SUMO está ativo.

No MVP, o comando principal é:

```text
trafficlight.setPhaseDuration
```

Isto permite:

- estender a fase corrente quando o corredor já está verde;
- encurtar a fase corrente para antecipar a transição do plano SUMO.

O TSP Safety Layer evita `setRedYellowGreenState` e evita saltos diretos de fase por defeito.

## Como executar sem SUMO

```bash
make validate
make test
make tsp-dryrun
```

Outputs esperados:

```text
outputs/cits_messages.jsonl
outputs/tsp_decisions.jsonl
outputs/tsp_actuation.jsonl
reports/tsp_emulation_summary.json
```

## Como executar com SUMO/TraCI

Requer rede compilada e SUMO instalado:

```bash
make build
make tsp-sumo
```

Modo SUMO sem aplicar comandos, útil para validação:

```bash
make tsp-sumo-no-actuation
```

## Resultado esperado em dry-run

O dry-run força três casos funcionais:

1. `green_extension` em I2: autocarro chega perto do fim do verde.
2. `early_green` em I6: autocarro chega com o corredor em vermelho e fase conflitante já cumpriu verde mínimo.
3. `no_action` em I4: o verde existente já é suficiente.

## Limitações conhecidas

- O mapeamento de fases ainda é proxy e está em `configs/tsp_config.json`.
- A calibração real depende de planos semafóricos municipais e signal groups reais.
- O early green é implementado como truncagem de duração da fase corrente; a sequência real de amarelo/all-red depende do programa SUMO existente.
- O asset ainda não otimiza offsets de corredor; essa etapa fica para Policy Optimization.
