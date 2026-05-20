# PPS57 Dashboard

Dashboard Streamlit para visualizar os artefactos dos Pacotes 3, 4 e 5.

## Executar

```bash
make platform
```

ou:

```bash
streamlit run dashboard/app.py
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
