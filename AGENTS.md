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

### Dependency notes
- `requirements.lock` is the **complete** reproducible lockfile: it includes SUMO
  interfaces, the full dashboard stack (`streamlit`, `streamlit-echarts`, `plotly`,
  `pandas`) and dev tools (`pytest`, `ruff`). Use it for repeatable installs
  (`python -m pip install -r requirements.lock`). `requirements.txt` declares
  flexible version ranges and is the source used to regenerate the lockfile.
- `starlette` and `uvicorn` appear in `requirements.lock` as transitive deps of
  `streamlit`; they are not direct project dependencies.

### Lint / test / build / run
- Lint: `.venv/bin/ruff check .` (and `ruff format .`). Ruff is the only linter.
- Test: `make test` (stdlib `unittest`, no SUMO needed) or `.venv/bin/python -m pytest -q`.
- Build + run synthetic corridor: `make build` then `make run` (needs venv on
  PATH). `make run` is slow (a couple of minutes) and writes a **multi-GB**
  `outputs/emissions.xml`; `make clean` removes generated outputs.
- Dashboard: `streamlit run dashboard.py` (default port 8501). Seven tabs: Resumo,
  KPIs, Decisão, C-ITS, vs RL, Documentação, Simulação. With an empty `reports/`
  most tabs show "no data" warnings (not errors). Generate data first: `make
  tsp-demonstrator` (Resumo/KPIs/Decisão/C-ITS tabs); `make compare-tsp-rl` (vs
  RL tab); `make scenario-suite` (KPIs tab scenario comparison, both arms, all
  seeds).
