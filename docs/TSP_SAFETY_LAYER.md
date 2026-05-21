# TSP Safety Layer — Motor de Decisão TSP e Safety Layer

## Objetivo

O TSP Safety Layer transforma os pedidos C-ITS aceites pela RSU em decisões semafóricas auditáveis e seguras. O objetivo é passar de um fluxo apenas comunicacional:

```text
OBU -> SREM-like -> RSU -> SSEM-like
```

para um fluxo operacional controlado:

```text
OBU -> RSU -> Motor TSP -> Safety Layer -> Atuador TraCI
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
src/pps57_tsp/signal_control.py
```

Foi implementado o `TraciTSPActuator`, que aplica comandos seguros via TraCI
quando o SUMO está ativo. O modo `--no-actuation` continua ligado ao SUMO/TraCI:
lê dados reais, calcula decisões e regista que a atuação não foi aplicada.

O atuador usa a fronteira `SignalControlAdapter`. A implementação atual é
`TraciSignalControlAdapter`, que encapsula SUMO/TraCI. Um controlador real deve
implementar a mesma fronteira sem alterar o `TSPDecisionEngine`.

Entre o TSP e o TraCI existe também `SimulatedControllerAdapter`, ativado por
`controller_simulation` em `configs/tsp_config.json`. Esta camada simula
comportamento típico de controlador real: modo manual/automático, latência,
comando pendente, intervalo mínimo entre comandos e rejeição determinística por
pedido pedonal ativo. Quando rejeita, o comando não chega ao TraCI e o log de
atuação inclui `controller_response`.

Antes de permitir atuação, o controller valida `controller_contracts` em
`configs/tsp_config.json` contra o programa semafórico lido por TraCI. O contrato
declara:

- signal groups que servem movimentos prioritários;
- fases SUMO associadas a cada signal group;
- matriz de conflitos;
- fases intergreen;
- limites por signal group (`min_green_s`, `max_green_s`, `max_extension_s`);
- se o programa deve ser de tempo fixo.

Além do contrato default, o ficheiro define contratos específicos por TLS em
`controller_contracts.controllers`, para evitar que a rede dependa de um proxy
global.

No MVP, o comando principal é:

```text
trafficlight.setPhaseDuration
```

Isto permite:

- estender a fase corrente quando o movimento prioritário já está verde;
- encurtar a fase corrente para antecipar a transição do plano SUMO.

O TSP Safety Layer evita `setRedYellowGreenState` e evita saltos diretos de fase por defeito.

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

## Limitações conhecidas

- O mapeamento de fases ainda é proxy e está em `configs/tsp_config.json`.
- A calibração real depende de planos semafóricos municipais e signal groups reais.
- O early green é implementado como truncagem de duração da fase corrente; a sequência real de amarelo/all-red depende do programa SUMO existente.
- O asset ainda não otimiza offsets de rede/coordenados; essa etapa fica para Policy Optimization.
