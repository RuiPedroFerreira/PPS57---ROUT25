# Handover — Pacote 6 Plataforma

## Estado entregue

O Pacote 6 adiciona uma plataforma Streamlit local que lê os outputs já gerados pelos Pacotes 3, 4 e 5. O objetivo é dar uma interface visual para validar o comportamento C-ITS/TSP e demonstrar KPIs.

## Ficheiros principais

```text
configs/platform_config.json
src/pps57_platform/data_loader.py
dashboard/app.py
scripts/check_platform_data.py
scripts/generate_platform_demo_data.py
tests/test_pacote6_platform.py
docs/PACOTE6_PLATAFORMA_DASHBOARD.md
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
