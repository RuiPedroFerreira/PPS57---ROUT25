# PPS57 ROUT25 Traffic Priority Platform

PPS57 ROUT25 is a local validation platform for public-transport traffic-signal
priority (TSP). It combines a SUMO digital twin, C-ITS/V2X message emulation, an
explainable TSP decision engine, a mandatory Safety Layer, offline policy
optimization, tabular reinforcement-learning training, and a Streamlit dashboard
for results comparison.

The platform's reference results now come from **Ingolstadt city-wide**:

- a **real, third-party calibrated city-wide network** — Ingolstadt (DE), the
  TUM-VT `sumo_ingolstadt` scenario — used as the baseline/reference for
  runner outputs, KPIs, dashboard scenario views and validation tests; and
- a **synthetic demonstration corridor** — a hand-built SUMO corridor themed on
  Porto/Boavista — kept as a small legacy/control arm for low-cost C-ITS/TSP
  experiments.

It is a technical demonstration and validation platform, **not** an operational
traffic-control deployment. The synthetic corridor's geometry, demand, PT lines
and signal plans are SUMO assets for simulation only; the Ingolstadt scenario is
real third-party data used as-is, never re-invented.

## What It Does

- Runs the explainable TSP engine **city-wide on the real Ingolstadt network**
  (123 signalised intersections, detector-calibrated demand, real GTFS public
  transport), with a plain SUMO baseline vs TSP actuation using paired seeds.
- Builds and runs a **synthetic SUMO corridor** scenario for controlled C-ITS/TSP
  demonstrations and the multi-scenario suite.
- Emulates C-ITS/V2X messages in JSON form: `MAPEM`, `SPATEM`, `SREM`, `SSEM`.
- Generates bus priority requests from emulated OBUs and validates them via RSUs.
- Converts accepted priority requests into TSP decisions.
- Applies a mandatory **Safety Layer** before any signal actuation.
- Validates a controller contract (signal groups, conflicts, intergreens, safety
  bounds) before enabling TraCI actuation, using the network's own authoritative
  conflict matrix (`<request foes>`).
- Screens approved TSP actions through a simulated controller layer that can
  ACK/NACK commands before they reach SUMO/TraCI.
- Supports SUMO/TraCI actuation and SUMO/TraCI no-actuation observation mode.
- Optionally activates the v2.2 priority event lifecycle (check-in/check-out with
  rolling green extensions and green compensation).
- Exports JSONL logs and JSON reports for auditability.
- Compares offline policy candidates against the baseline TSP engine and trains a
  tabular Q-learning policy from SUMO/TraCI event-derived scenarios.
- Loads exported policies for runtime inference in semi-live TSP runs.
- Presents results in a **Streamlit dashboard** (`dashboard.py`).

## Two Scenarios: Real City-Wide vs Synthetic Corridor

The platform keeps a clear line between the two arms; they are not the same model.

| | Ingolstadt city-wide | Synthetic corridor |
|---|---|---|
| Geometry | Real OSM-derived net (TUM-VT), 123 TLS | Hand-built corridor, ~7 intersections |
| Demand | Detector-calibrated real routes | Synthetic, HCM-anchored profiles |
| Public transport | Real GTFS (INVG): 59 lines, 2138 trips/day, 418 stops | Synthetic proxy lines |
| Signal plans | Real `TL`/`WAUT` programs | Generated fixed-time plans |
| Used for | External validity, scale, honest grounding | Controlled C-ITS/TSP demo, scenario suite, dashboard |
| Entry point | `scripts/run_ingolstadt_demo.py` | `make build`, `scripts/run_sumo_scenario.py`, `make tsp-demonstrator` |
| Catalog | `configs/scenario_catalog_ingolstadt.yaml` | `configs/scenario_catalog.yaml` |

The synthetic corridor is themed "Porto/Boavista" in identifiers and example
payloads. That theme is cosmetic: the geometry is generic synthetic SUMO assets,
**not** a model of Porto and **not** Ingolstadt.

## Repository Layout

```text
configs/                 Runtime config for C-ITS, TSP, policy training, scenarios
dashboard.py             Streamlit results-comparison dashboard
public/                  Dashboard branding assets (logos)
outputs/                 Generated JSONL/XML/log artifacts (git-ignored)
reports/                 Generated summaries, KPIs and policy reports (git-ignored)
scripts/                 User-facing command-line entry points
scripts/legacy_porto/    Archived Porto/Boavista sim-to-real exploration (see its README)
src/pps57_cits/          C-ITS/V2X emulation layer
src/pps57_opt/           Policy optimization, runtime policy loading and RL training
src/pps57_sumo/          SUMO network generation, KPI parsing, network binding/profile
src/pps57_tsp/           TSP engine, Safety Layer and actuation
sumo/                    Synthetic-corridor SUMO network, routes, additionals, run config
tests/                   Unit and integration tests
.tools/                  Git-ignored downloads (TUM-VT Ingolstadt clone, fetched data)
```

## Documentation Map

- [Prerequisites](#prerequisites) and [Environment Setup](#environment-setup)
  cover the local Python/SUMO/dashboard setup.
- [First Run Quick Start](#first-run-quick-start) gives the shortest paths.
- [Ingolstadt City-Wide Scenario](#ingolstadt-city-wide-scenario-tum-vt) covers
  the real-network run.
- [Streamlit Dashboard](#streamlit-dashboard) covers `dashboard.py`.
- [Available Make Targets](#available-make-targets) is the command reference.
- [Command-Line Workflows](#command-line-workflows) describes each pipeline and
  its artifacts.
- [Output Artifacts](#output-artifacts), [Safety Layer Rules](#safety-layer-rules)
  and [Runtime Policy Modes](#runtime-policy-modes) explain the runtime evidence
  model.
- [Real-Scenario Grounding & Honest Limitations](#real-scenario-grounding--honest-limitations)
  records what is real, what is synthetic and what is not claimed.
- [Troubleshooting](#troubleshooting) lists the most common issues.

## Prerequisites

Run commands from the repository root. The project expects:

- Python 3.11 or 3.12.
- A local virtual environment at `.venv`.
- SUMO command-line tools reachable from `PATH` for simulation workflows:
  `sumo`, `sumo-gui` and `netconvert`.
- For the Ingolstadt scenario: a local clone of the TUM-VT scenario (see below).
- For the dashboard: `streamlit`, `plotly` and `pandas` (included in the
  pinned dependency set).

The pinned dependency set includes the SUMO Python interfaces and, where the
`eclipse-sumo` wheel supports the platform, SUMO CLI binaries inside `.venv/bin`.
Activating `.venv` before running `make` makes those binaries available to the
Makefile. If your platform does not get CLI binaries from the wheel, install
SUMO separately and put its binaries on `PATH`.

## Environment Setup

Use the lockfile for reproducible local installs:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
```

Verify the environment:

```bash
python -m pip check
python -m unittest discover -s tests -p 'test_*.py'
sumo --version
netconvert --version
```

Use `requirements.txt` only when intentionally refreshing dependency ranges:

```bash
python -m pip install -r requirements.txt
python -m pip freeze > requirements.lock
```

`requirements.txt` declares flexible version ranges. `requirements.lock`
captures the full transitive closure at exact versions for repeatable local
validation, releases, CI and dependency regression triage.

## First Run Quick Start

After activating `.venv`, run the fast checks first:

```bash
make validate
make test
make scenario-list
```

For a synthetic-corridor baseline SUMO run and KPI export:

```bash
make build
make run
make kpis
```

For the real city-wide reference scenario (after cloning TUM-VT — see below):

```bash
make ingolstadt-list
make ingolstadt-smoke
```

To explore results in the dashboard:

```bash
streamlit run dashboard.py
```

If `make build`, `make run` or a scenario command cannot find `sumo` or
`netconvert`, activate the virtual environment in the current shell or add an
external SUMO installation to `PATH`.

## Ingolstadt City-Wide Scenario (TUM-VT)

The real-network arm runs the TSP engine across the **whole signalised network of
Ingolstadt (DE)**, using the third-party, calibrated
[TUM-VT `sumo_ingolstadt`](https://github.com/TUM-VT/sumo_ingolstadt) scenario
(Apache-2.0). Nothing is fabricated: the network, motorised demand
(detector-calibrated `routes_<day>_24h_det_calib`), traffic-light programs and
public transport (real INVG GTFS) all come from the scenario as-is.

### Obtain the scenario

```bash
git clone --depth 1 https://github.com/TUM-VT/sumo_ingolstadt.git .tools/ingolstadt
```

`.tools/` is git-ignored. `scripts/run_ingolstadt_demo.py` then copies the chosen
day's files into a clean working directory `.tools/ingol_run` — a path with no
spaces and no `---`, which the SUMO command-line tools require (they echo file
paths into XML comments, where `--` is illegal).

### Run baseline vs TSP

```bash
# list real day × window slices from configs/scenario_catalog_ingolstadt.yaml
.venv/bin/python scripts/run_ingolstadt_demo.py --list

# 5-minute smoke: plain SUMO baseline vs TSP actuation on the AM peak
.venv/bin/python scripts/run_ingolstadt_demo.py \
  --scenario city_am_peak --run-type pair --steps 300 --seeds 57

# full catalog suite, paired by seed
.venv/bin/python scripts/run_ingolstadt_demo.py --all --run-type pair --seeds 57
```

The `baseline` arm is plain SUMO without the TSP runtime. `tsp_no_actuation` is
kept as a dry-run/counterfactual audit mode, not as the baseline. The runner
auto-discovers all 123 TLS, builds the authoritative conflict matrix, writes
isolated SUMO outputs under `.tools/ingol_run/runs/<scenario>/<run_type>/seed_<n>/out/`,
and writes KPIs/reports under `reports/ingolstadt/`.

### Scenario set and method

`configs/scenario_catalog_ingolstadt.yaml` documents the city-wide scenario set:
each scenario is a real **slice** (day × time window) of the calibrated data —
e.g. `city_am_peak` (07:00–09:00 on 2023-07-04), `city_midday_offpeak`,
`city_pm_secondary_peak`, and `weekend_sunday` (2023-06-25). The representative
demand window is ~05:00–14:00; there is no afternoon peak on the demo day.

KPIs are reported at two levels: (1) the whole network (external validity, scale)
and (2) per line, with **Line 11** as a clean causal-attribution lens (25
consecutive signalised intersections). Operational situations (delayed bus,
bunching, cross-traffic pressure, multi-line conflict) **emerge** from the
simulation and are measured, not constructed. The TSP config's `corridor` block
is disabled city-wide (a single recovery-debt budget summed over 123 TLS is
meaningless); the pairwise downstream spillback-defer is kept.

## Streamlit Dashboard

`dashboard.py` defaults to the **Ingolstadt city-wide reference** scenario reports
when `reports/ingolstadt/` exists, with the synthetic corridor still available as
a fallback dataset. Launch it with:

```bash
streamlit run dashboard.py
```

It has eight tabs — Resumo, KPIs, Decisão, C-ITS, vs RL, Cenários, Método and
Simulação — and the Cenários tab reads `reports/ingolstadt/` plus
`configs/scenario_catalog_ingolstadt.yaml` by default. Generate those first with
`make ingolstadt-smoke`, `make ingolstadt-run` or `make ingolstadt-suite`.

## Available Make Targets

| Target | Purpose | SUMO required |
|---|---|---|
| `make validate` | Validate project structure and JSON configs | No |
| `make test` | Run all unit tests | No |
| `make sumo-smoke` | Quick SUMO binary smoke test | Yes |
| `make ingolstadt-list` | List real Ingolstadt reference slices | No |
| `make ingolstadt-smoke` | Run a short Ingolstadt baseline-vs-TSP pair | Yes + TUM clone |
| `make ingolstadt-run` | Run one Ingolstadt reference scenario | Yes + TUM clone |
| `make ingolstadt-suite` | Run every Ingolstadt reference scenario | Yes + TUM clone |
| `make build` | Generate synthetic-corridor plain files and compile the network | Yes |
| `make run` | Build and run the synthetic-corridor baseline | Yes |
| `make gui` | Build and open the SUMO GUI baseline | Yes |
| `make kpis` | Parse `outputs/tripinfo.xml` into `reports/baseline_kpis.json` | Needs tripinfo |
| `make scenario-list` | List configured SUMO validation scenarios and estimated demand | No |
| `make scenario-run SCENARIO=baseline_am_peak RUN_TYPE=baseline` | Generate, run and export KPIs for one scenario/run type | Yes |
| `make scenario-suite RUN_TYPE=baseline` | Run every configured scenario and export per-scenario KPIs | Yes |
| `make cits-sumo` | Run C-ITS emulation connected to SUMO/TraCI | Yes |
| `make tsp-demonstrator` | Run SUMO baseline, direct TSP and TSP with simulated controller, then write evidence reports | Yes |
| `make tsp-sumo` | Run TSP with SUMO and TraCI actuation | Yes |
| `make tsp-sumo-no-actuation` | Run TSP with SUMO observation only | Yes |
| `make tsp-gui` | Run TSP with SUMO GUI and TraCI actuation | Yes |
| `make tsp-gui-no-actuation` | Run TSP with SUMO GUI observation only | Yes |
| `make compare-tsp-rl` | Run baseline and RL TSP back-to-back and write a comparison table | Yes |
| `make compare-sumo-kpis BASELINE_KPIS=... RL_KPIS=...` | Diff two pre-generated KPI JSON files | No |
| `make evaluate-decision-outcomes` | Classify RL decisions as same / blocked-unsafe / less intrusive | Yes |
| `make build-event-training-dataset` | Join C-ITS/TSP/actuation logs into the event training dataset | No |
| `make optimize-offline` | Compare safe offline policy candidates | No |
| `make train-rl-policy` | Train tabular Q-learning policy offline | No |
| `make sort-routes` | Sort SUMO route definitions by departure time | No |
| `make clean` | Remove known generated artifacts | No |

The synthetic corridor targets remain available for controlled legacy/demo runs;
Ingolstadt is the reference path for scenario KPIs and dashboard scenario results.

## Choosing A Workflow

| Goal | Start with |
|---|---|
| Validate the checkout without running SUMO | `make validate` and `make test` |
| Confirm the synthetic scenario catalog | `make scenario-list` |
| Build the synthetic SUMO network | `make build` |
| Produce baseline SUMO KPIs | `make run` followed by `make kpis` |
| Run TSP city-wide on the real network | `scripts/run_ingolstadt_demo.py` |
| Generate C-ITS messages from SUMO/TraCI | `make cits-sumo` |
| Observe TSP decisions without changing signals | `make tsp-sumo-no-actuation` |
| Run TSP with TraCI signal actuation | `make tsp-sumo` |
| Compare baseline, direct TSP and controller-mediated TSP | `make tsp-demonstrator` |
| Train or refresh runtime policies | `make build-event-training-dataset`, then `make optimize-offline` or `make train-rl-policy` |
| Explore results visually | `streamlit run dashboard.py` |

## Command-Line Workflows

### Static Validation

```bash
make validate
```

Checks the repository structure and validates the main JSON configs:

```text
configs/cits_v2x_config.json
configs/tsp_safety_config.json
configs/policy_training_config.json
configs/validation_config.json
```

### Run Tests

```bash
make test
```

Equivalent direct command:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Pytest is also supported:

```bash
.venv/bin/python -m pytest -q
```

### Build SUMO Network (synthetic corridor)

```bash
make build
```

Direct equivalent:

```bash
.venv/bin/python src/pps57_sumo/build_network.py \
  --config configs/sumo_scenario_base.json \
  --base-dir sumo
```

`make build` runs `make validate` first, so the gate that checks well-formed XML
and sorted routes fires before any network compilation. This builds the
**synthetic corridor**; the Ingolstadt network is downloaded pre-built (see
[Ingolstadt City-Wide Scenario](#ingolstadt-city-wide-scenario-tum-vt)).

### Run SUMO Baseline (synthetic corridor)

```bash
make run
make kpis
```

Generated outputs:

```text
outputs/tripinfo.xml
outputs/summary.xml
outputs/statistics.xml
reports/baseline_kpis.json
```

### Run Scenario Suite (synthetic corridor)

The synthetic scenario suite is declared in `configs/sumo_scenario_base.json` and
described in `configs/scenario_catalog.yaml`. Each scenario has a demand profile,
public-transport service assumptions, event configuration and KPI focus.

```bash
make scenario-list
make scenario-run SCENARIO=baseline_am_peak
make scenario-run SCENARIO=cross_traffic_pressure RUN_TYPE=comparison
make scenario-suite RUN_TYPE=baseline
```

Supported run types are `baseline`, `cits`, `tsp_no_actuation`,
`tsp_actuation`, `comparison` (baseline + no-actuation + actuation), and `all`.

Scenario KPIs include tripinfo metrics (duration, speed, waiting time,
timeLoss, bus headways and priority/general/emergency vehicle groups) plus E1/E2
detector-derived queue metrics. Each run gets a verdict and scenarios with a
baseline plus TSP run get automatic baseline-vs-TSP comparisons.

Per-scenario artifacts are written under:

```text
outputs/scenarios/<scenario_id>/<run_type>/seed_<seed>/
reports/scenarios/<scenario_id>/<run_type>/seed_<seed>/kpis.json
reports/scenarios/<scenario_id>/scenario_report.md
reports/scenarios/scenario_suite_summary.json
reports/scenarios/scenario_suite_report.md
```

When a scenario defines multiple seeds, or when `--seeds` is passed to
`scripts/run_sumo_scenario.py`, each replication is stored under its own
`seed_<seed>` directory and the scenario report includes aggregate KPI
statistics. Each aggregated KPI carries the mean, sample standard deviation,
standard error and a 95% confidence interval (Student's t), plus p5/p95 spread.

For scenarios run with multiple seeds in both the baseline and a TSP arm, the
baseline-vs-TSP comparison adds a **paired significance test** on bus
`mean_time_loss_s`: per-seed improvements are paired by seed and reported as a
mean improvement with a 95% confidence interval, with a verdict of
`significant_improvement`, `significant_regression` or
`inconclusive_ci_includes_zero` (significant only when the interval excludes
zero). A single-seed run cannot support a significance claim and omits this
block. Enable multi-seed for a scenario by adding `random_seeds` to its profile
in `configs/sumo_scenario_base.json`.

When running C-ITS/TSP modes in restricted environments, set a fixed TraCI port:

```bash
TRACI_PORT=8813 make scenario-run SCENARIO=baseline_off_peak RUN_TYPE=tsp_no_actuation
```

### Run C-ITS/V2X Emulation

With SUMO/TraCI:

```bash
make build
.venv/bin/python scripts/run_cits_emulation.py --mode sumo --steps 7200
```

With SUMO GUI:

```bash
make build
.venv/bin/python scripts/run_cits_emulation.py --mode sumo --gui --steps 7200
```

Generated outputs:

```text
outputs/cits_messages.jsonl
outputs/cits_mapem_snapshot.json
outputs/cits_spatem_snapshot.json
reports/cits_emulation_summary.json
```

### Run TSP Control With Safety Layer

SUMO with TraCI actuation:

```bash
make build
.venv/bin/python scripts/run_tsp_control.py --mode sumo --steps 7200
```

SUMO observation mode, without applying signal commands:

```bash
make build
.venv/bin/python scripts/run_tsp_control.py --mode sumo --steps 7200 --no-actuation
```

SUMO GUI:

```bash
make build
.venv/bin/python scripts/run_tsp_control.py --mode sumo --gui --steps 7200
```

#### Actuation Seam (field-readiness boundary)

The decision, Safety Layer and actuation code talk to controllers through the
`SignalControlAdapter` Protocol (`src/pps57_tsp/signal_control.py`), not to TraCI
directly. SUMO/TraCI is one implementation (`TraciSignalControlAdapter`, with a
`SimulatedControllerAdapter` controller shim on top); the real `traci`/`libsumo`
runtime is reached **only** inside `src/pps57_cits`. `tests/test_actuation_seam.py`
enforces this isolation (it fails if any other library module imports or calls
the TraCI/libsumo modules), so a future real-controller adapter (e.g. NTCIP
1202/1211) is a clean drop-in. **NTCIP is a planned adapter, not a built
component** — this platform is SUMO-only and not an operational deployment.

#### Priority Event Lifecycle (v2.2, opt-in)

The v2.2 priority event lifecycle replaces single-shot TSP decisions with a
check-in/check-out model:

- **Check-in** is implicit: the first green extension applied for a (TLS, vehicle)
  pair opens a priority event and records the original phase end.
- **Rolling extensions** use the normal decision loop. The OBU refreshes its SREM
  at `request_refresh_s` intervals; each increment is bounded by
  `green_extension_rolling_increment_s` and validated by the Safety Layer. Continued
  extensions of the same event do not consume cooldown budget or count as new
  interventions, but the cumulative event budget never exceeds `max_green_extension_s`.
- **Check-out** fires when the request store marks the request cleared or expired:
  the event restores the original phase end, returning unused green to the cross
  movement. Green compensation (`src/pps57_tsp/compensation.py`) shortens the next
  occurrence of the same phase to reclaim time donated by extensions.

Enable v2.2 by using `configs/tsp_safety_config_v22.json` instead of the default:

```bash
.venv/bin/python scripts/run_tsp_control.py \
  --mode sumo \
  --steps 7200 \
  --config configs/tsp_safety_config_v22.json
```

#### Runtime Policy Inference

Runtime inference from an exported policy:

```bash
.venv/bin/python scripts/run_tsp_control.py \
  --mode sumo \
  --steps 7200 \
  --policy-mode optimized \
  --policy-report reports/policy_report.json
```

Runtime inference from an exported RL policy:

```bash
make train-rl-policy
.venv/bin/python scripts/run_tsp_control.py \
  --mode sumo \
  --steps 7200 \
  --policy-mode rl
```

### Baseline vs RL Comparison

```bash
make compare-tsp-rl
```

This writes:

```text
reports/tsp_baseline_vs_rl_comparison.md
reports/tsp_baseline_vs_rl_comparison.json
```

To diff two pre-generated KPI files without re-running SUMO:

```bash
make compare-sumo-kpis BASELINE_KPIS=reports/baseline_kpis.json RL_KPIS=reports/rl_kpis.json
```

### Realistic Demonstrator

```bash
make tsp-demonstrator
```

This runs three SUMO/TraCI scenarios and snapshots their artifacts under
`outputs/demonstrator/run-*`:

```text
sumo_baseline     SUMO baseline without TSP intervention
tsp               TSP direct TraCI actuation
tsp_controller    TSP through the simulated controller contract
```

It writes:

```text
reports/tsp_demonstrator_report.md
reports/tsp_demonstrator_report.json
```

The report checks public-transport KPIs, general-traffic cost, Safety Layer
blocks, controller ACK/NACK evidence and per-TLS runtime counts. It consumes only
SUMO/TraCI artifacts generated by those runs, and is the data source for the
dashboard.

### Decision Outcome Evaluation

```bash
make evaluate-decision-outcomes
```

This writes:

```text
reports/decision_outcome_evaluation.md
reports/decision_outcome_evaluation.json
```

The evaluator is intentionally conservative. Without paired SUMO KPI reports it
can classify decisions as same, blocked/unsafe, or less intrusive, but it keeps
network impact as `inconclusive_without_kpis`.

### Protocol Lifecycle Audit

Replays SREM/SSEM/TSP/actuation logs and checks the per-request lifecycle chain:

```bash
.venv/bin/python scripts/audit_protocol_lifecycle.py \
  --cits outputs/cits_messages.jsonl \
  --decisions outputs/tsp_decisions.jsonl \
  --actuations outputs/tsp_actuation.jsonl \
  --output reports/protocol_lifecycle_audit.json
```

Run a TSP SUMO workflow first to produce the input logs.

### Event-Log Training Dataset

```bash
make build-event-training-dataset
```

This writes `outputs/event_training_dataset.jsonl` by joining C-ITS messages,
TSP decisions and actuation logs produced by SUMO/TraCI.
Run a TSP SUMO workflow first; an empty or missing event dataset is rejected by
policy optimization and RL training.

Generated outputs from a TSP run:

```text
outputs/tsp_decisions.jsonl
outputs/tsp_actuation.jsonl
reports/tsp_emulation_summary.json
```

In `--mode sumo`, runtime RL decisions receive instantaneous TraCI network
features per TLS: queue/halting counts, lane vehicle count, mean speed, waiting
time, occupancy, spillback risk and active TSP request count.

### Run Offline Policy Optimization

```bash
make optimize-offline
```

Direct equivalent:

```bash
.venv/bin/python scripts/run_policy_optimization.py
```

What it does:

- Requires `outputs/event_training_dataset.jsonl` from a previous TSP SUMO run.
- Loads SUMO/TraCI event-derived scenarios from `outputs/event_training_dataset.jsonl`.
- Evaluates the baseline TSP decision.
- Evaluates candidate actions: `no_action`, `green_extension`, `early_green`, `reevaluate_next_cycle`, `reject`.
- Sends every candidate through the Safety Layer.
- Exports the best safe action per state bucket.
- Marks the method as offline policy comparison, not reinforcement learning.

Generated outputs:

```text
outputs/offline_policy_samples.jsonl
outputs/policy_candidates.jsonl
reports/policy_report.json
reports/policy_optimization_summary.json
```

### Train Tabular Reinforcement Learning Policy

```bash
make train-rl-policy
```

Direct equivalent:

```bash
.venv/bin/python scripts/run_rl_training.py
```

What it does:

- Requires `outputs/event_training_dataset.jsonl` from a previous TSP SUMO run.
- Runs tabular Q-learning over SUMO/TraCI event-derived scenarios.
- Keeps production online learning disabled.
- Exports a policy report that can be inspected or loaded for runtime inference.
- Keeps the Safety Layer as a mandatory guardrail.

Generated outputs:

```text
reports/tabular_q_policy_report.json
reports/rl_training_summary.json
```

### NetworkBinding Check

Demonstrates that the authoritative conflict matrix (from SUMO junction `<request
foes>`) resolves the fail-close gate on the **real Ingolstadt network**. Requires
the Ingolstadt net to be materialised first (run `scripts/run_ingolstadt_demo.py`
after cloning TUM-VT):

```bash
.venv/bin/python scripts/run_network_binding_check.py
```

Counts signal groups that trip the fail-close predicate before and after the
NetworkBinding is applied. Evidence is written to
`reports/validation/networkbinding_ingolstadt_check.json`.

### Empirical Network Profile Check

Starts SUMO via TraCI, compares the extracted `NetworkProfile` against the
traffic-light programs actually loaded by SUMO, and optionally applies one
approved TSP actuation to observe the real phase sequence:

```bash
.venv/bin/python scripts/empirical_network_profile_check.py \
  --network .tools/ingol_run/ingolstadt_net.net.xml
.venv/bin/python scripts/empirical_network_profile_check.py \
  --network .tools/ingol_run/ingolstadt_net.net.xml --apply-actuation
```

## C-ITS/V2X Message Flow

The emulated message flow is:

```text
MAPEM: RSU publishes intersection topology
SPATEM: RSU publishes signal state
SREM: OBU requests, updates or cancels priority
SSEM: RSU responds with processing, rejected, granted or unknown/cancelled status
```

Messages are JSON/Python-native for functional validation in SUMO. The v0.4
shape follows ETSI MAPEM/SPATEM/SREM/SSEM concepts, but it is not ASN.1/OER/UPER
encoding, does not use operational PKI, and remains a simulation profile.

The simulation interface contract separates standard-like fields, operator
extensions, SUMO artifacts, synthetic geometry, trust assumptions and lifecycle
audit expectations.

Optional broker transport effects are configured in
`configs/cits_v2x_config.json` under `message_transport`. They can inject
seeded latency, jitter, loss, duplicate delivery and reordering while keeping
the default run ideal and deterministic.

SPATEM `signal_group_id` values map to ASN.1 `signalGroupID` (valid range 1–255).
Junctions with more than 255 SUMO links have their excess links silently dropped
per MovementEvent rather than invalidating the whole message.

## Message Examples

The payloads below are **illustrative**, using synthetic-corridor identifiers
(`RSU_BOAVISTA_02`, `STCP500_PROXY_W`). The Ingolstadt run uses the same shapes
with that scenario's auto-discovered IDs.

### MAPEM

Describes the topology of one controlled intersection.

```json
{
  "message_type": "MAPEM",
  "protocol_version": "0.4.0",
  "source_id": "RSU_BOAVISTA_02",
  "destination_id": "BROADCAST",
  "message_id": "mapem-demo-001",
  "station_id": 2076096511,
  "station_type": 15,
  "moy": 0,
  "timestamp_ms": 0,
  "generation_delta_time_ms": 0,
  "intersection_ref_id": 2,
  "intersection_alias": "I2",
  "tls_id": "I2",
  "rsu_id": "RSU_BOAVISTA_02",
  "intersection_name": "Boavista I2",
  "revision": 1,
  "ref_point": {
    "latitude_e7": 410000200,
    "longitude_e7": -85992500,
    "elevation_dm": 0
  },
  "security": {
    "signer_id": "RSU_BOAVISTA_02",
    "certificate_id": "simulated",
    "signature_b64": null,
    "generation_time_ms": 0,
    "valid_until_ms": 60000
  },
  "approaches": [
    {
      "approach_id": "I2:I1_I2",
      "edge_id": "I1_I2",
      "direction": "westbound",
      "priority_movement_ids": ["I2_westbound_public_transport"],
      "lane_ids": ["I1_I2_0"]
    }
  ]
}
```

### SPATEM

Describes movement states and remaining timing context.

```json
{
  "message_type": "SPATEM",
  "protocol_version": "0.4.0",
  "source_id": "RSU_BOAVISTA_02",
  "destination_id": "BROADCAST",
  "message_id": "spatem-demo-001",
  "station_id": 2076096511,
  "station_type": 15,
  "moy": 1,
  "timestamp_ms": 40000,
  "generation_delta_time_ms": 34464,
  "security": {
    "signer_id": "RSU_BOAVISTA_02",
    "certificate_id": "simulated",
    "signature_b64": null,
    "generation_time_ms": 100000,
    "valid_until_ms": 160000
  },
  "intersection_ref_id": 2,
  "intersection_alias": "I2",
  "tls_id": "I2",
  "revision": 1,
  "movement_events": [
    {
      "signal_group_id": 1,
      "event_state": "protected-Movement-Allowed",
      "min_end_time_ms": 32000,
      "max_end_time_ms": 32000,
      "likely_time_ms": 32000,
      "confidence": 15
    }
  ],
  "intersection_status": {},
  "debug_sumo_state": "GGrr"
}
```

### SREM

Represents a priority request, update or cancellation sent by an emulated OBU.

```json
{
  "message_type": "SREM",
  "protocol_version": "0.4.0",
  "source_id": "OBU_bus_I2",
  "destination_id": "RSU_BOAVISTA_02",
  "message_id": "srem-demo-001",
  "station_id": 123456,
  "station_type": 6,
  "moy": 1,
  "timestamp_ms": 40000,
  "generation_delta_time_ms": 34464,
  "security": {
    "signer_id": "OBU_bus_I2",
    "certificate_id": "simulated",
    "signature_b64": null,
    "generation_time_ms": 100000,
    "valid_until_ms": 112000
  },
  "sequence_number": 7,
  "requests": [
    {
      "intersection_ref_id": 2,
      "request_id": 3,
      "request_type": "priorityRequest",
      "in_bound_lane_id": "I1_I2_0",
      "out_bound_lane_id": "I2_I3",
      "eta_min_minute": 1,
      "eta_min_second_ms": 56000,
      "duration_ms": 12000
    }
  ],
  "requestor": {
    "station_id": 123456,
    "station_type": 6,
    "basic_vehicle_role": "publicTransport",
    "position": {"latitude_e7": 0, "longitude_e7": 0, "elevation_dm": 0},
    "heading_deg": 0.0,
    "speed_mps": 10.0,
    "route_name": "STCP500_PROXY_W",
    "operational_vehicle_id": "bus_I2"
  },
  "operator_telemetry": {
    "schedule_delay_s": 120.0,
    "headway_deviation_s": 0.0,
    "distance_to_stopline_m": 160.0,
    "eta_to_stopline_s": 16.0,
    "operator_priority_class": "high_delay",
    "line_id": "STCP500_PROXY_W",
    "route_id": "route_boavista_proxy",
    "intersection_alias": "I2",
    "tls_id": "I2",
    "rsu_id": "RSU_BOAVISTA_02",
    "priority_movement_id": "I2_westbound_public_transport",
    "target_signal_group_id_hint": "I2_priority_westbound"
  },
  "expires_at_s": 112.0
}
```

### SSEM

Represents the RSU response to an `SREM` request.

```json
{
  "message_type": "SSEM",
  "protocol_version": "0.4.0",
  "source_id": "RSU_BOAVISTA_02",
  "destination_id": "OBU_bus_I2",
  "message_id": "ssem-demo-001",
  "correlation_id": "srem-demo-001",
  "station_id": 2076096511,
  "station_type": 15,
  "moy": 1,
  "timestamp_ms": 40200,
  "generation_delta_time_ms": 34664,
  "security": {
    "signer_id": "RSU_BOAVISTA_02",
    "certificate_id": "simulated",
    "signature_b64": null,
    "generation_time_ms": 100200,
    "valid_until_ms": 160200
  },
  "intersection_ref_id": 2,
  "intersection_alias": "I2",
  "tls_id": "I2",
  "rsu_id": "RSU_BOAVISTA_02",
  "response": {
    "request_id": 3,
    "sequence_number": 7,
    "requestor_station_id": 123456,
    "response_status": "processing",
    "granted_signal_group": null,
    "valid_until_ms": 115200
  },
  "audit": {
    "granted_strategy": "none",
    "rejection_reason": null,
    "confidence": 0.95,
    "notes": []
  }
}
```

## TSP Decision And Actuation Examples

Example TSP decision after Safety Layer approval:

```json
{
  "timestamp_s": 100.0,
  "request_id": "request-demo-001",
  "vehicle_id": "bus_I2",
  "intersection_id": "I2",
  "tls_id": "I2",
  "rsu_id": "RSU_BOAVISTA_02",
  "action": "green_extension",
  "status": "approved",
  "reason": "extend_current_green_to_cover_bus_eta",
  "priority_score": 0.5,
  "requested_maneuver": "green_extension",
  "eta_to_stopline_s": 16.0,
  "schedule_delay_s": 120.0,
  "headway_deviation_s": 0.0,
  "extension_s": 12.0,
  "current_phase_index": 0,
  "current_signal_state": "GGrr",
  "notes": ["Safety Layer approved green extension."]
}
```

Example SUMO/TraCI actuation result:

```json
{
  "decision_id": "decision-demo-001",
  "timestamp_s": 100.0,
  "tls_id": "I2",
  "action": "green_extension",
  "applied": true,
  "no_actuation": false,
  "command": "setPhaseDuration",
  "reason": "traci_green_extension_applied",
  "parameters": {
    "extension_s": 12.0
  }
}
```

## Output Artifacts

| Artifact | Producer | Meaning |
|---|---|---|
| `outputs/cits_messages.jsonl` | C-ITS and TSP runs | MAPEM/SPATEM/SREM/SSEM event stream |
| `outputs/cits_mapem_snapshot.json` | C-ITS runs | Latest generated MAPEM-like topology snapshot |
| `outputs/cits_spatem_snapshot.json` | C-ITS runs | Latest generated SPATEM-like signal snapshot |
| `outputs/tsp_decisions.jsonl` | TSP runs | TSP decisions after Safety Layer validation |
| `outputs/tsp_actuation.jsonl` | TSP runs | SUMO/TraCI actuation or no-actuation observation events |
| `outputs/offline_policy_samples.jsonl` | Policy optimization | Offline training/evaluation scenarios |
| `outputs/policy_candidates.jsonl` | Policy optimization | Candidate action evaluations |
| `outputs/event_training_dataset.jsonl` | `build-event-training-dataset` | Joined C-ITS/TSP/actuation scenarios for policy training |
| `outputs/tripinfo.xml` | SUMO baseline | Per-vehicle SUMO trip information |
| `outputs/scenarios/<scenario>/<run_type>/seed_<seed>/` | Scenario runner | Per-scenario SUMO, C-ITS and TSP run artifacts |
| `.tools/ingol_run/out/` | `scripts/run_ingolstadt_demo.py` | Ingolstadt city-wide run logs (decisions, actuation, C-ITS, summaries) |
| `reports/cits_emulation_summary.json` | C-ITS runs | Message counts and request summary |
| `reports/tsp_emulation_summary.json` | TSP runs | Decision, safety and actuation summary |
| `reports/protocol_lifecycle_audit.json` | `scripts/audit_protocol_lifecycle.py` | Replayed SREM/SSEM/TSP/actuation lifecycle audit |
| `reports/baseline_kpis.json` | KPI parser | SUMO baseline KPI summary |
| `reports/scenarios/<scenario>/<run_type>/seed_<seed>/kpis.json` | Scenario runner | Per-run KPI summary used by scenario reports |
| `reports/validation/networkbinding_ingolstadt_check.json` | `scripts/run_network_binding_check.py` | Conflict-matrix coverage and fail-close removal on the Ingolstadt net |
| `reports/policy_report.json` | Policy optimization | Exported safe runtime policy |
| `reports/policy_optimization_summary.json` | Policy optimization | Offline comparison summary |
| `reports/tabular_q_policy_report.json` | RL training | Exported tabular Q-learning policy |
| `reports/rl_training_summary.json` | RL training | RL training metrics |
| `reports/tsp_demonstrator_report.md/.json` | `make tsp-demonstrator` | Three-arm demonstrator evidence (dashboard data source) |
| `reports/tsp_baseline_vs_rl_comparison.md/.json` | `make compare-tsp-rl` | Baseline vs RL KPI comparison |
| `reports/decision_outcome_evaluation.md/.json` | `make evaluate-decision-outcomes` | Per-decision outcome classification |

## Safety Layer Rules

The Safety Layer is mandatory for baseline decisions, optimized runtime decisions and offline policy candidates. It blocks or downgrades decisions when safety conditions are not met, including:

- current yellow transition;
- unsafe phase sequence;
- minimum green not satisfied;
- maximum green extension exceeded;
- maximum total green exceeded;
- cooldown after previous priority intervention;
- maximum consecutive priority interventions per signal;
- expired or non-actuable requests;
- signal groups without an authoritative conflict matrix (fail-closed).

In v2.2 event lifecycle mode, continued rolling extensions of the same event do not
consume cooldown budget or count as new interventions, but the cumulative event
budget is still bounded by `max_green_extension_s`.

The current runtime-policy path only proposes a decision. It does not bypass safety validation.

## Runtime Policy Modes

`scripts/run_tsp_control.py` supports three policy modes:

| Mode | Behavior |
|---|---|
| `baseline` | Uses the explainable TSP decision engine directly |
| `optimized` | Loads an exported policy report and proposes runtime actions before Safety Layer validation |
| `rl` | Loads the exported RL policy report, by default `reports/tabular_q_policy_report.json`, and proposes runtime actions before Safety Layer validation |

Example:

```bash
.venv/bin/python scripts/run_tsp_control.py \
  --mode sumo \
  --policy-mode optimized \
  --policy-report reports/policy_report.json
```

The RL training path remains outside the TSP Decision Engine:

```text
SUMO/TraCI event logs -> event training dataset -> RL training -> exported policy report -> runtime inference -> Safety Layer
```

## Configuration Files

| Config | Purpose |
|---|---|
| `configs/sumo_scenario_base.json` | Synthetic-corridor geometry, demand, PT services, stops and SUMO generation inputs |
| `configs/scenario_catalog.yaml` | Synthetic-corridor scenario descriptors, demand profiles and KPI focus |
| `configs/scenario_catalog_ingolstadt.yaml` | Ingolstadt city-wide scenario set (real day × window slices, method, measured facts) |
| `configs/cits_v2x_config.json` | OBU, RSU, C-ITS logging and safety constraints (synthetic corridor) |
| `configs/cits_ingolstadt_config.json` | C-ITS/TSP config for the real Ingolstadt run (network auto-discovery, GTFS schedule plan) |
| `configs/tsp_safety_config.json` | TSP scoring, actuation and runtime policy settings (v2.1, default) |
| `configs/tsp_safety_config_v22.json` | TSP safety settings with v2.2 priority event lifecycle enabled |
| `configs/policy_training_config.json` | Candidate actions, reward and RL training settings |
| `configs/validation_config.json` | Source-traced validation thresholds (GEH, flow/travel-time bands, TSP face-validity) |

## Real-Scenario Grounding & Honest Limitations

The platform's external-validity grounding is the **real, calibrated Ingolstadt
network** (TUM-VT, Apache-2.0). No demand, timetable or geometry is invented:
each city-wide scenario is a real slice (day × window) of detector-calibrated
data with real INVG GTFS public transport. Source-traced acceptance thresholds
live in `configs/validation_config.json` (each carries a `source` field), kept
independent of SUMO so the oracle is not the system under test.

What is **not** claimed:

- C-ITS messages are a JSON simulation profile — not ASN.1/OER and not operational
  PKI; MAPEM geometry anchors are synthetic WGS84-like, not surveyed coordinates.
- The TSP only actuates on signal groups with an authoritative conflict matrix.
  On the Ingolstadt net that is ~57.9% of signal groups (814/1407); all 123 TLS
  are addressable (none in total fail-close) after the `via`→slot fix in
  `network_binding`, and the Safety Layer fail-closes everywhere else.
- The synthetic corridor's magnitudes are illustrative (HCM-anchored), not a
  calibration of any specific city.

### Legacy Porto sim-to-real exploration

An earlier phase tried to ground the simulation by *constructing* a real Porto/
Boavista corridor from open data (OSM extract, STCP GTFS, a European reference-
count envelope — the V2/V3/V4 "ladder"). That approach was superseded by the
ready-made calibrated Ingolstadt scenario, so its construction scripts have no
analog here (there is nothing to download/netconvert/snap when the network is
already built and calibrated). They are archived, not deleted, under
[`scripts/legacy_porto/`](scripts/legacy_porto/README.md) and remain recoverable
and auditable. The reusable, city-agnostic parts (authoritative conflict matrix,
empirical network profile, GTFS and reference-count parsers) stayed on the main
path under `src/pps57_sumo/`.

## Development Checks

Before sharing changes:

```bash
make validate
make test
.venv/bin/python -m pytest -q
git diff --check
```

## Troubleshooting

### `sumo` or `netconvert` is missing

Simulation targets call SUMO binaries by name. Activate the virtual environment
before running `make`, or install SUMO separately and add its binaries to `PATH`:

```bash
source .venv/bin/activate
which sumo
which netconvert
sumo --version
netconvert --version
```

If `sumo-gui` is unavailable or the machine is headless, use the non-GUI targets
such as `make run`, `make cits-sumo` or `make tsp-sumo-no-actuation`.

### The Ingolstadt scenario is not found

`scripts/run_ingolstadt_demo.py` expects the TUM-VT scenario cloned into
`.tools/ingolstadt`:

```bash
git clone --depth 1 https://github.com/TUM-VT/sumo_ingolstadt.git .tools/ingolstadt
```

The runner materialises each day's files into `.tools/ingol_run`, a clean path
with no spaces and no `---`. This matters: the repository directory name contains
`---`, and SUMO CLI tools echo their file paths into XML comments where `--` is
illegal, so absolute paths through the repo root break those tools. Keep SUMO
inputs/outputs under `.tools/ingol_run`.

### The dashboard shows no data

`dashboard.py` reads generated reports. Produce them first, e.g.:

```bash
make tsp-demonstrator
make compare-tsp-rl
make scenario-suite RUN_TYPE=comparison
streamlit run dashboard.py
```

### Policy optimization or RL training says no event scenarios were found

The policy commands require `outputs/event_training_dataset.jsonl` with rows
derived from TSP decisions, SPATEM context and actuation logs:

```bash
make tsp-sumo-no-actuation
make build-event-training-dataset
make optimize-offline
make train-rl-policy
```

### Scenario runs need a fixed TraCI port

```bash
TRACI_PORT=8813 make scenario-run SCENARIO=baseline_off_peak RUN_TYPE=tsp_no_actuation
```

### Optimized runtime policy is not loaded

Regenerate the policy report, then pass it explicitly:

```bash
make optimize-offline
.venv/bin/python scripts/run_tsp_control.py --mode sumo --policy-mode optimized --policy-report reports/policy_report.json
```
