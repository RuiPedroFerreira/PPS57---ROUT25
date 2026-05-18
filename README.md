# PPS57 — ROUT25 Traffic Priority Asset

## Estado atual

Este repositório contém a evolução técnica do asset PPS57 para simulação, emulação C-ITS/V2X e preparação de prioridade semafórica inteligente para transporte público.

A base atual inclui:

- **Pacote 2 — Gémeo Digital SUMO v0.2**, com cenário proxy realista Porto/Boavista;
- **Pacote 3 — Emulação C-ITS/V2X v0.3**, com OBUs, RSUs e mensagens MAPEM-like, SPATEM-like, SREM-like e SSEM-like.

## Cenário realista Porto/Boavista

O cenário deixou de ser um corredor genérico e passou a ser um **proxy topológico realista** inspirado no corredor urbano da Avenida da Boavista, no Porto, entre Casa da Música e a zona Praça do Império/Castelo do Queijo.

> Importante: este cenário ainda **não é uma rede operacional calibrada**. É uma base de desenvolvimento técnica, com geometria aproximada, linhas de autocarro proxy e procura sintética de hora de ponta. A passagem a gémeo digital real exige importação OSM/GTFS e dados reais de contagens e semáforos.

## Estrutura principal

```text
configs/
  calibration_targets.yaml
  corridor_config.json
  corridor_config_porto_boavista_realistic.json
  cits_config.json
  scenarios.yaml
  signal_policy_constraints.yaml

docs/
  CALIBRACAO_DADOS_REAIS.md
  CENARIO_REALISTA_BOAVISTA.md
  FONTES_DADOS_REAIS.md
  HANDOVER_PACOTE3.md
  HANDOVER_PACOTE4.md
  PACOTE3_CITS_V2X.md

scripts/
  run_baseline.py
  run_cits_emulation.py

src/
  pps57_sumo/
    generate_plain_corridor.py
    parse_tripinfo.py
    validate_project.py
  pps57_cits/
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

sumo/
  plain/corredor.nod.xml
  plain/corredor.edg.xml
  routes/routes.rou.xml
  additional/bus_stops.add.xml
  additional/detectors.add.xml
  corredor.sumocfg

tests/
  test_pacote3_cits.py
```

## Como correr validações sem SUMO

A validação estática e a emulação C-ITS em modo `dry-run` não exigem SUMO:

```bash
make validate
make test
make cits-dryrun
```

O modo `dry-run` gera:

```text
outputs/cits_messages.jsonl
outputs/cits_mapem_snapshot.json
outputs/cits_spatem_snapshot.json
reports/cits_emulation_summary.json
```

## Como correr a simulação SUMO baseline

Requer SUMO instalado, com `netconvert`, `sumo` e `sumo-gui` no PATH.

```bash
make build
make run
make kpis
```

Ou:

```bash
python scripts/run_baseline.py
```

## Como correr a emulação C-ITS com SUMO/TraCI

Depois de compilar a rede:

```bash
make build
python scripts/run_cits_emulation.py --mode sumo --steps 7200
```

Com GUI:

```bash
python scripts/run_cits_emulation.py --mode sumo --gui --steps 7200
```

## Pacote 3 — Emulação C-ITS/V2X

O Pacote 3 implementa o fluxo:

```text
Autocarro / OBU emulada
        ↓ SREM-like
Broker C-ITS em memória
        ↓
RSU da interseção
        ↓ SSEM-like
OBU / motor de decisão TSP futuro
```

Mensagens implementadas:

- `MAPEM_like`: topologia funcional da interseção;
- `SPATEM_like`: estado semafórico observado ou snapshot estático;
- `SREM_like`: pedido de prioridade enviado pela OBU;
- `SSEM_like`: resposta da RSU.

No Pacote 3, a RSU **não altera semáforos**. Quando aceita um pedido, devolve a ação `forward_to_decision_engine`. A atuação semafórica segura via TraCI fica reservada para o Pacote 4.

## Próximo passo

O próximo desenvolvimento técnico é o **Pacote 4 — Motor de decisão TSP e Safety Layer**, que deverá transformar pedidos aceites em ações semafóricas seguras, por exemplo extensão de verde, antecipação de verde ou rejeição fundamentada.
