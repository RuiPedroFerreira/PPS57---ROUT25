# Validation Platform — Plataforma de Validação e Demonstração

## Objetivo

O Validation Platform transforma os artefactos técnicos dos C-ITS, TSP Safety Layer e Policy Optimization numa plataforma local de validação e demonstração. A plataforma não substitui o `sumo-gui`; complementa-o ao explicar a lógica C-ITS, as decisões TSP, a Safety Layer, as atuações semafóricas e os resultados da otimização offline.

## Componentes

```text
configs/platform_config.json
src/pps57_platform/data_loader.py
src/pps57_platform/runner.py
src/pps57_platform/api.py
dashboard/app.py
scripts/run_platform_api.py
scripts/check_platform_data.py
scripts/generate_platform_demo_data.py
tests/test_platform_dashboard.py
```

## Comandos

Gerar dados demo quando ainda não existem outputs de simulação:

```bash
make platform-demo-data
```

Validar a disponibilidade dos artefactos:

```bash
make platform-check
```

Abrir a dashboard Streamlit:

```bash
make platform
```

Arrancar a API local de controlo:

```bash
make platform-api
```

Alias equivalente:

```bash
make dashboard
```

## Fluxo recomendado

Para demonstrar sem SUMO:

```bash
make cits-dryrun
make tsp-dryrun
make optimize-offline
make platform-check
make platform
```

Para demonstrar com dados artificiais mínimos:

```bash
make platform-demo-data
make platform-check
make platform
```

Para demonstrar com SUMO/SUMO-GUI:

```bash
make tsp-gui-no-actuation
make optimize-offline
make platform
```

## Dados lidos pela plataforma

A plataforma lê os seguintes artefactos quando existem:

```text
outputs/cits_messages.jsonl
outputs/tsp_decisions.jsonl
outputs/tsp_actuation.jsonl
outputs/offline_policy_samples.jsonl
outputs/policy_candidates.jsonl
reports/cits_emulation_summary.json
reports/tsp_emulation_summary.json
reports/baseline_kpis.json
reports/policy_optimization_summary.json
reports/policy_report.json
outputs/tripinfo.xml
```

## Páginas da dashboard

- **Overview**: cartões de resumo e estado global.
- **C-ITS**: mensagens MAPEM-like, SPATEM-like, SREM-like e SSEM-like.
- **TSP & Safety**: ações TSP, estados, motivos e bloqueios de segurança.
- **Atuação**: comandos aplicados, dry-run vs TraCI e TLS afetados.
- **Otimização**: candidatos do Policy Optimization, política selecionada e reward delta.
- **KPIs**: baseline, TripInfo e métricas de mobilidade.
- **Controlo**: consola Streamlit para enviar comandos à API local.
- **Artefactos**: disponibilidade dos ficheiros esperados.

## API local de controlo

A API FastAPI é uma camada intermédia: a dashboard envia comandos HTTP e a API
arranca processos locais (`tsp-dry-run`, `cits-dry-run`, `optimize-offline`,
`platform-demo-data`, etc.). O Streamlit não abre sockets TraCI nem controla
SUMO diretamente.

Endpoints principais:

```text
GET  /health
GET  /runs/current
POST /runs/start
POST /runs/stop
POST /runs/pause
POST /runs/resume
GET  /artifacts/snapshot
GET  /events/recent
```

## Limitações atuais

- A dashboard continua compatível com modo offline/pós-simulação.
- Não há autenticação, multiutilizador ou backend persistente.
- O carregamento é feito a partir de ficheiros locais JSON/JSONL/XML.
- A API local é para demonstração e controlo single-host; produção exigiria
  autenticação, autorização e gestão explícita de concorrência multiutilizador.

## Próximo passo sugerido

O passo seguinte é criar comparação entre cenários, por exemplo:

```text
baseline sem TSP
vs TSP rule-based
vs TSP com otimização offline
vs política RL/proxy
```

A mesma estrutura poderá evoluir para uma base de dados leve, como SQLite ou PostgreSQL, caso seja necessário reter execuções históricas.
