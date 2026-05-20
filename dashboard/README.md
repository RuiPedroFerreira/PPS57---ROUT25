# PPS57 Dashboard

Dashboard Streamlit para visualizar os artefactos dos C-ITS, TSP Safety Layer e Policy Optimization.

## Executar

```bash
make platform
```

ou:

```bash
streamlit run dashboard/app.py
```

Para ativar o separador de controlo, arranca também a API local noutro terminal:

```bash
make platform-api
```

## Preparar dados

```bash
make cits-dryrun
make tsp-dryrun
make optimize-offline
make platform-check
```

Para uma demonstração rápida sem SUMO:

```bash
make platform-demo-data
make platform
```
