# PPS57 - Pacote 2 Starter Kit SUMO v0.2

## Cenário realista Porto/Boavista

Este repositório contém uma evolução do starter kit inicial. O cenário deixou de ser um corredor genérico e passou a ser um **proxy topológico realista** inspirado no corredor urbano da Avenida da Boavista, no Porto, entre Casa da Música e a zona Praça do Império/Castelo do Queijo.

> Importante: este cenário ainda **não é uma rede operacional calibrada**. É uma base de desenvolvimento técnica, com geometria aproximada, linhas de autocarro proxy e procura sintética de hora de ponta. A passagem a gémeo digital real exige importação OSM/GTFS e dados reais de contagens e semáforos.

## O que mudou face à v0.1

- Interseções passaram a ter nomes plausíveis do corredor Boavista.
- A geometria deixou de ser totalmente linear e ganhou espaçamentos variáveis.
- A simulação passou de 1h para 2h de hora de ponta da manhã.
- Foram adicionadas duas linhas proxy de transporte público, baseadas na lógica das linhas STCP 500/502.
- A procura passou a ter assimetria inbound/outbound.
- Foram adicionados detetores E1 e E2 em aproximações de interseções.
- Foram adicionadas restrições semafóricas para preparar os Pacotes 3 e 4.
- Foi documentado um plano de substituição por dados OSM/GTFS/contagens reais.

## Como correr localmente

Requer SUMO instalado, com `netconvert`, `sumo` e `sumo-gui` no PATH.

```bash
make validate
make build
make run
make kpis
```

Ou:

```bash
python scripts/run_baseline.py
```

## Estrutura principal

```text
configs/
  corridor_config.json
  corridor_config_porto_boavista_realistic.json
  calibration_targets.yaml
  signal_policy_constraints.yaml
  scenarios.yaml
sumo/
  plain/corredor.nod.xml
  plain/corredor.edg.xml
  routes/routes.rou.xml
  additional/bus_stops.add.xml
  additional/detectors.add.xml
  corredor.sumocfg
src/pps57_sumo/
  generate_plain_corridor.py
  validate_project.py
  parse_tripinfo.py
```

## Próximo passo

No Pacote 3, este cenário deve ser ligado a:

- emulador OBU por autocarro;
- agente RSU por interseção;
- mensagens SREM-like/SSEM-like;
- adaptador TraCI para ler estados semafóricos e aplicar decisões.
