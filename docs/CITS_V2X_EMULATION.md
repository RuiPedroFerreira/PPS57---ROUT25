# PPS57 — C-ITS/V2X emulation: Emulação C-ITS/V2X

## Objetivo

O C-ITS/V2X emulation adiciona ao cenário SUMO do SUMO Digital Twin uma camada de emulação C-ITS/V2X para suportar pedidos de prioridade semafórica por transportes públicos.

A entrega implementa o fluxo funcional:

```text
Autocarro em SUMO / OBU emulada
        ↓ SREM-like
Broker C-ITS em memória
        ↓
RSU da interseção
        ↓ SSEM-like
OBU / camada futura de decisão TSP
```

Também são geradas mensagens de contexto:

- `MAPEM_like`: topologia da interseção e aproximações controladas;
- `SPATEM_like`: estado semafórico observado;
- `SREM_like`: pedido de prioridade enviado pelo autocarro;
- `SSEM_like`: resposta da RSU ao pedido.

> Nota: as mensagens são JSON e são compatíveis a nível funcional com a lógica C-ITS, mas não implementam codificação ASN.1/UPER operacional. Essa conversão só deve ser feita numa fase de integração com stack C-ITS real.

## Componentes adicionados

```text
configs/cits_config.json
src/pps57_cits/
  broker.py
  config.py
  controller.py
  event_logger.py
  map_spat.py
  messages.py
  models.py
  obu.py
  rsu.py
  traci_adapter.py
scripts/run_cits_emulation.py
tests/test_cits_emulation.py
```

## Como executar sem SUMO

O modo `dry-run` valida a camada C-ITS sem arrancar a simulação SUMO:

```bash
make cits-dryrun
```

ou:

```bash
python scripts/run_cits_emulation.py --mode dry-run --steps 60
```

Este modo gera:

```text
outputs/cits_messages.jsonl
outputs/cits_mapem_snapshot.json
outputs/cits_spatem_snapshot.json
reports/cits_emulation_summary.json
```

## Como executar com SUMO/TraCI

Depois de instalar SUMO e compilar a rede:

```bash
make build
python scripts/run_cits_emulation.py --mode sumo --steps 7200
```

Com interface gráfica:

```bash
python scripts/run_cits_emulation.py --mode sumo --gui --steps 7200
```

## Política OBU

A OBU emulada gera um pedido `SREM_like` quando o veículo:

1. é identificado como autocarro/linha prioritária;
2. está numa aproximação controlada por RSU;
3. está a menos de `request_distance_m` da interseção;
4. tem ETA dentro da janela configurada;
5. cumpre critério de atraso ou desvio de headway;
6. não enviou pedido equivalente dentro do período de refresh.

Parâmetros principais em `configs/cits_config.json`:

```json
"obu_policy": {
  "request_distance_m": 250,
  "request_eta_min_s": 8,
  "request_eta_max_s": 45,
  "delay_threshold_s": 60,
  "headway_deviation_threshold_s": 120,
  "request_refresh_s": 5,
  "request_ttl_s": 12
}
```

## Política RSU

A RSU processa pedidos recebidos e devolve `SSEM_like` com:

- `acknowledged`, se o pedido é elegível;
- `rejected`, se o pedido está expirado, fora da janela ETA, em cooldown ou sem critério de prioridade.

No C-ITS/V2X emulation, a ação aceite é:

```text
forward_to_decision_engine
```

Isto é deliberado: a RSU ainda não altera semáforos. A atuação semafórica segura via TraCI deve ser implementada no TSP Safety Layer.

## Limitações assumidas

- Não há ainda codificação C-ITS ASN.1 real.
- Não há ainda comunicação rádio ITS-G5/C-V2X real.
- Não há ainda atuação semafórica, apenas emulação de pedidos e respostas.
- O cálculo de atraso usa `demo_force_bus_delay_s` no modo inicial, para garantir geração de pedidos em ambiente de desenvolvimento.
- A leitura de estado semafórico via TraCI só funciona se a rede estiver compilada e SUMO estiver instalado.

## Critérios de aceitação do C-ITS/V2X emulation

- O sistema gera `MAPEM_like` para as 7 interseções.
- O sistema gera `SPATEM_like` em modo dry-run e SUMO.
- A OBU gera `SREM_like` quando um autocarro elegível entra na janela de pedido.
- A RSU gera `SSEM_like` com aceitação ou rejeição justificada.
- Todas as mensagens ficam registadas em JSONL.
- Existe relatório resumido de mensagens.
- Existem testes unitários para broker, mensagens, OBU, RSU e dry-run.
