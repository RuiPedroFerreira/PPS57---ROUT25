# Handover — Validation Platform Plataforma

## Estado entregue

O Validation Platform adiciona uma plataforma Streamlit local que lê os outputs já gerados pelos C-ITS, TSP Safety Layer e Policy Optimization. O objetivo é dar uma interface visual para validar o comportamento C-ITS/TSP e demonstrar KPIs.

## Ficheiros principais

```text
configs/platform_config.json
src/pps57_platform/data_loader.py
dashboard/app.py
scripts/check_platform_data.py
scripts/generate_platform_demo_data.py
tests/test_platform_dashboard.py
docs/PLATFORM_DASHBOARD.md
```

## Comandos úteis

```bash
make validate
make test
make cits-dryrun
make tsp-dryrun
make optimize-offline
make platform-check
make platform
```

## Critérios de aceitação

- `make test` executa os testes da plataforma.
- `make platform-check` gera `reports/platform_snapshot.json`.
- `make platform` abre a dashboard local.
- A dashboard deve mostrar mensagens C-ITS, decisões TSP, atuações, candidatos de otimização e artefactos disponíveis.

## Evoluções recomendadas

- Adicionar comparação multi-cenário.
- Guardar execuções históricas em SQLite/PostgreSQL.
- Integrar refresh semi-live durante `sumo-gui`.
- Exportar relatório executivo em PDF/HTML.
