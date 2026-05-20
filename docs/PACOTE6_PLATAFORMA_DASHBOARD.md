# Pacote 6 — Plataforma de Validação e Demonstração

## Objetivo

O Pacote 6 transforma os artefactos técnicos dos Pacotes 3, 4 e 5 numa plataforma local de validação e demonstração. A plataforma não substitui o `sumo-gui`; complementa-o ao explicar a lógica C-ITS, as decisões TSP, a Safety Layer, as atuações semafóricas e os resultados da otimização offline.

## Componentes

```text
configs/platform_config.json
src/pps57_platform/data_loader.py
dashboard/app.py
scripts/check_platform_data.py
scripts/generate_platform_demo_data.py
tests/test_pacote6_platform.py
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
outputs/pacote5_offline_samples.jsonl
outputs/pacote5_policy_candidates.jsonl
reports/cits_emulation_summary.json
reports/tsp_emulation_summary.json
reports/baseline_kpis.json
reports/pacote5_optimization_summary.json
reports/pacote5_policy_report.json
outputs/tripinfo.xml
```

## Páginas da dashboard

- **Overview**: cartões de resumo e estado global.
- **C-ITS**: mensagens MAPEM-like, SPATEM-like, SREM-like e SSEM-like.
- **TSP & Safety**: ações TSP, estados, motivos e bloqueios de segurança.
- **Atuação**: comandos aplicados, dry-run vs TraCI e TLS afetados.
- **Otimização**: candidatos do Pacote 5, política selecionada e reward delta.
- **KPIs**: baseline, TripInfo e métricas de mobilidade.
- **Artefactos**: disponibilidade dos ficheiros esperados.

## Limitações atuais

- A primeira versão é offline/pós-simulação.
- Não há autenticação, multiutilizador ou backend persistente.
- O carregamento é feito a partir de ficheiros locais JSON/JSONL/XML.
- A visualização em tempo real pode ser implementada depois com refresh automático ou backend de eventos.

## Próximo passo sugerido

O passo seguinte é criar comparação entre cenários, por exemplo:

```text
baseline sem TSP
vs TSP rule-based
vs TSP com otimização offline
vs política RL/proxy
```

A mesma estrutura poderá evoluir para uma base de dados leve, como SQLite ou PostgreSQL, caso seja necessário reter execuções históricas.
