# PPS57 — ROUT25 Traffic Priority Asset

## Estado atual

Este repositório contém a evolução técnica do asset PPS57 para simulação, emulação C-ITS/V2X e prioridade semafórica inteligente para transporte público.

A base atual inclui:

- **Pacote 2 — Gémeo Digital SUMO v0.2**, com cenário proxy realista Porto/Boavista;
- **Pacote 3 — Emulação C-ITS/V2X v0.3**, com OBUs, RSUs e mensagens MAPEM-like, SPATEM-like, SREM-like e SSEM-like;
- **Pacote 4 — Motor de Decisão TSP + Safety Layer v0.4**, com decisões de green extension, early green, no action, rejeição e atuação segura via dry-run/TraCI;
- **Pacote 5 — Otimização offline + RL tabular proxy v0.5**, com baseline TSP explicável e Safety Layer como filtro obrigatório para qualquer política otimizada.

## Cenário realista Porto/Boavista

O cenário é um **proxy topológico realista** inspirado no corredor urbano da Avenida da Boavista, no Porto, entre Casa da Música e a zona Praça do Império/Castelo do Queijo.

> Importante: este cenário ainda **não é uma rede operacional calibrada**. É uma base de desenvolvimento técnica, com geometria aproximada, linhas de autocarro proxy e procura sintética de hora de ponta. A passagem a gémeo digital real exige importação OSM/GTFS e dados reais de contagens e semáforos.

## Estrutura principal

```text
configs/
  calibration_targets.yaml
  corridor_config.json
  corridor_config_porto_boavista_realistic.json
  cits_config.json
  tsp_config.json
  optimization_config.json
  scenarios.yaml
  signal_policy_constraints.yaml

docs/
  CALIBRACAO_DADOS_REAIS.md
  CENARIO_REALISTA_BOAVISTA.md
  FONTES_DADOS_REAIS.md
  PACOTE3_CITS_V2X.md
  PACOTE4_TSP_SAFETY_LAYER.md
  PACOTE5_OTIMIZACAO_RL_OFFLINE.md
  HANDOVER_PACOTE5.md

scripts/
  run_baseline.py
  run_cits_emulation.py
  run_tsp_control.py
  run_pacote5_optimization.py

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
  pps57_tsp/
    actuator.py
    config.py
    controller.py
    engine.py
    logger.py
    models.py
    safety.py
  pps57_opt/
    config.py
    dataset.py
    models.py
    optimizer.py

sumo/
  plain/corredor.nod.xml
  plain/corredor.edg.xml
  routes/routes.rou.xml
  additional/bus_stops.add.xml
  additional/detectors.add.xml
  corredor.sumocfg

tests/
  test_pacote3_cits.py
  test_pacote4_tsp.py
```

## Como correr validações sem SUMO

A validação estática, a emulação C-ITS e o TSP dry-run não exigem SUMO:

```bash
make validate
make test
make cits-dryrun
make tsp-dryrun
make optimize-offline
```

O `tsp-dryrun` gera:

```text
outputs/cits_messages.jsonl
outputs/tsp_decisions.jsonl
outputs/tsp_actuation.jsonl
outputs/pacote5_offline_samples.jsonl
outputs/pacote5_policy_candidates.jsonl
reports/tsp_emulation_summary.json
reports/pacote5_policy_report.json
reports/pacote5_optimization_summary.json
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

## Como correr o Pacote 4 — TSP + Safety Layer

Sem SUMO:

```bash
make tsp-dryrun
```

Com SUMO e atuação TraCI:

```bash
make tsp-sumo
```

Com SUMO, mas sem aplicar comandos semafóricos:

```bash
make tsp-sumo-no-actuation
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
OBU / motor de decisão TSP
```

Mensagens implementadas:

- `MAPEM_like`: topologia funcional da interseção;
- `SPATEM_like`: estado semafórico observado ou snapshot estático;
- `SREM_like`: pedido de prioridade enviado pela OBU;
- `SSEM_like`: resposta da RSU.

## Pacote 4 — Motor de Decisão TSP e Safety Layer

O Pacote 4 implementa o fluxo:

```text
SREM-like aceite pela RSU
        ↓
Motor de decisão TSP
        ↓
Safety Layer
        ↓
Atuador dry-run ou TraCI
```

Ações suportadas:

- `green_extension`: prolonga o verde corrente quando o autocarro chegaria no fim do verde;
- `early_green`: encurta a fase corrente para antecipar o verde do corredor;
- `no_action`: mantém plano atual quando o verde já é suficiente;
- `reevaluate_next_cycle`: adia a decisão por segurança;
- `reject`: rejeita intervenção por pontuação insuficiente ou pedido expirado.

A safety layer bloqueia decisões que violem limites configurados, como verde máximo, verde mínimo, cooldown, intervenções consecutivas e transições em amarelo.

## Pacote 5 — Otimização offline e RL proxy

O Pacote 5 executa uma avaliação offline de políticas candidatas:

```bash
make optimize-offline
```

O fluxo compara candidatos `no_action`, `green_extension`, `early_green`, `reevaluate_next_cycle` e `reject` contra o baseline TSP do Pacote 4. Cada candidato passa pela Safety Layer antes de poder ser selecionado. Candidatos bloqueados por segurança são registados, mas não entram na política exportada.

## Próximo passo

O próximo desenvolvimento técnico é substituir o dataset sintético do Pacote 5 por logs SUMO calibrados e dados reais, mantendo a Safety Layer como filtro obrigatório antes de qualquer atuação.
