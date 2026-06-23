# AGENTS.md

## Cursor Cloud specific instructions

This is a Python (3.12) SUMO traffic-priority (TSP) simulation platform plus a
Streamlit results dashboard. There is no compiled app/server to keep running; you
invoke CLI pipelines (`make` targets / `scripts/*.py`) and the dashboard on
demand. Standard commands are documented in `README.md` ("Available Make Targets",
"Command-Line Workflows") and the `Makefile`; the notes below only cover
non-obvious, durable setup/run caveats.

### Environment / how things are wired
- All work happens in the project virtualenv `.venv`. The startup update script
  creates it and installs deps. The `Makefile` auto-detects `.venv/bin/python`,
  so `make <target>` uses the venv even without activation.
- SUMO is **not** a system package here: the `eclipse-sumo` wheel ships the
  `sumo`, `sumo-gui` and `netconvert` binaries inside `.venv/bin`. SUMO targets
  call these by name, so you must put the venv on `PATH` first, e.g.
  `export PATH="$PWD/.venv/bin:$PATH"` (or `source .venv/bin/activate`).
  Otherwise `make build` / `make run` fail with "sumo not found".
- `SUMO_HOME` is auto-detected by the wheel and is **not** required for normal
  runs (`make build/run/sumo-smoke`, tests). Only set
  `SUMO_HOME="$PWD/.venv/lib/python3.12/site-packages/sumo"` if you use a
  `$SUMO_HOME/tools`-based helper such as `make sort-routes`.
- System dependency `python3.12-venv` (apt) is required to create `.venv` and is
  already provisioned in the VM snapshot; it is intentionally **not** in the
  update script.

### Dependency caveat (important)
- `requirements.lock` is **incomplete** for development: it omits the dashboard
  deps (`streamlit`, `plotly`, `pandas`) and lists unused packages
  (`fastapi`/`uvicorn`/`starlette` are not imported anywhere). `requirements.txt`
  is the real dev dependency set (SUMO interfaces + dashboard). The update script
  installs `requirements.txt` plus `ruff` (linter, configured in `pyproject.toml`
  but unpinned) and `pytest`.

### Lint / test / build / run
- Lint: `.venv/bin/ruff check .` (and `ruff format .`). Ruff is the only linter.
- Test: `make test` (stdlib `unittest`, no SUMO needed) or `.venv/bin/python -m pytest -q`.
- Build + run synthetic corridor: `make build` then `make run` (needs venv on
  PATH). `make run` is slow (a couple of minutes) and writes a **multi-GB**
  `outputs/emissions.xml`; `make clean` removes generated outputs.
- Dashboard: `streamlit run dashboard.py` (default port 8501). It reads generated
  reports under `reports/`; with an empty `reports/` most tabs show "no data"
  warnings (not errors). Generate data first, e.g. `make tsp-demonstrator`
  (populates `reports/tsp_demonstrator_report.json`, `*_kpis.json`) for the
  Resumo/KPIs tabs; `make compare-tsp-rl` for "vs RL"; `make scenario-suite
  RUN_TYPE=comparison` for "Cenários".
