# PPS57 ROUT25 Traffic Priority Platform

PPS57 ROUT25 is a local validation platform for public-transport traffic-signal priority on a simulated Porto/Boavista corridor model. It combines a SUMO digital twin, C-ITS/V2X message emulation, an explainable TSP decision engine, a mandatory Safety Layer, offline policy optimization, tabular reinforcement-learning training, and a scenario dashboard.

The repository is designed for technical demonstration and validation. It is not an operational traffic-control deployment: network geometry, demand, public-transport lines and signal plans are synthetic SUMO assets for simulation only.

## What It Does

- Builds and runs a SUMO corridor scenario for Porto/Boavista.
- Emulates C-ITS/V2X messages in JSON form: `MAPEM`, `SPATEM`, `SREM`, and `SSEM`.
- Generates bus priority requests from emulated OBUs and validates them through RSUs.
- Converts accepted priority requests into TSP decisions.
- Applies a Safety Layer before any signal actuation.
- Validates a controller contract with signal groups, conflicts, intergreens and
  safety bounds before enabling TraCI actuation.
- Screens approved TSP actions through a simulated controller layer that can
  ACK/NACK commands before they reach SUMO/TraCI.
- Supports SUMO/TraCI actuation and SUMO/TraCI no-actuation observation mode.
- Exports JSONL logs and JSON reports for auditability.
- Generates a demonstrator report comparing SUMO baseline, direct TSP and TSP
  through the simulated controller, using only SUMO/TraCI artifacts.
- Compares offline policy candidates against the baseline TSP engine.
- Trains a tabular Q-learning policy from SUMO/TraCI event-derived scenarios.
- Loads exported policies for runtime inference in semi-live TSP runs.
- Serves a local scenario dashboard for running scenarios and comparing KPIs.

## Repository Layout

```text
configs/                 Runtime configuration for C-ITS, TSP, policy training and platform checks
docs/                    Protocol ICD and design notes
outputs/                 Generated JSONL/XML/log artifacts
reports/                 Generated summaries, KPIs and policy reports
scripts/                 User-facing command-line entry points
src/pps57_cits/          C-ITS/V2X emulation layer
src/pps57_opt/           Policy optimization, runtime policy loading and RL training
src/pps57_platform/      FastAPI runner and artifact aggregation
src/pps57_sumo/          SUMO network generation and KPI parsing
src/pps57_tsp/           TSP engine, Safety Layer and actuation
sumo/                    SUMO network, routes, additional files and run config
tests/                   Unit and integration tests
```

## Documentation Map

- [Prerequisites](#prerequisites) and [Environment Setup](#environment-setup)
  cover the local Python/SUMO setup.
- [First Run Quick Start](#first-run-quick-start) gives the shortest validation,
  simulation and dashboard paths.
- [Available Make Targets](#available-make-targets) is the command reference for
  common project workflows.
- [Command-Line Workflows](#command-line-workflows) describes each pipeline and
  its generated artifacts.
- [Output Artifacts](#output-artifacts), [Safety Layer Rules](#safety-layer-rules)
  and [Runtime Policy Modes](#runtime-policy-modes) explain the runtime evidence
  model.
- [Troubleshooting](#troubleshooting) lists the most common setup and runtime
  issues.

## Prerequisites

Run commands from the repository root. The project expects:

- Python 3.11 or 3.12.
- A local virtual environment at `.venv`.
- SUMO command-line tools reachable from `PATH` for simulation workflows:
  `sumo`, `sumo-gui` and `netconvert`.

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

For a baseline SUMO run and KPI export:

```bash
make build
make run
make kpis
```

For C-ITS/TSP event data, run one of the SUMO/TraCI workflows:

```bash
make cits-sumo
make tsp-sumo-no-actuation
```

Build learning data and policy reports after those logs exist:

```bash
make build-event-training-dataset
make optimize-offline
make train-rl-policy
make platform-check
```

Start the local scenario dashboard:

```bash
make dashboard
```

Default URLs:

```text
Dashboard: http://127.0.0.1:8000
API docs:  http://127.0.0.1:8000/docs
```

If `make build`, `make run` or a scenario command cannot find `sumo` or
`netconvert`, activate the virtual environment in the current shell or add an
external SUMO installation to `PATH`.

## Available Make Targets

| Target | Purpose | SUMO required |
|---|---|---|
| `make validate` | Validate project structure and JSON configs | No |
| `make test` | Run all unit tests | No |
| `make build` | Generate SUMO plain files and compile the network | Yes |
| `make run` | Build and run the SUMO baseline | Yes |
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
| `make optimize-offline` | Compare safe offline policy candidates | No |
| `make train-rl-policy` | Train tabular Q-learning policy offline | No |
| `make platform-check` | Aggregate and validate platform artifacts | No |
| `make dashboard` | Start the local scenario dashboard and API | No |
| `make sort-routes` | Sort SUMO route definitions by departure time | No |
| `make clean` | Remove known generated artifacts | No |

## Choosing A Workflow

| Goal | Start with |
|---|---|
| Validate the checkout without running SUMO | `make validate` and `make test` |
| Confirm the scenario catalog | `make scenario-list` |
| Build the SUMO network | `make build` |
| Produce baseline SUMO KPIs | `make run` followed by `make kpis` |
| Generate C-ITS messages from SUMO/TraCI | `make cits-sumo` |
| Observe TSP decisions without changing signals | `make tsp-sumo-no-actuation` |
| Run TSP with TraCI signal actuation | `make tsp-sumo` |
| Compare baseline, direct TSP and controller-mediated TSP | `make tsp-demonstrator` |
| Train or refresh runtime policies | `make build-event-training-dataset`, then `make optimize-offline` or `make train-rl-policy` |
| Inspect artifacts through the local UI | `make dashboard` |

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
configs/platform_config.json
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

### Build SUMO Network

```bash
make build
```

Direct equivalent:

```bash
.venv/bin/python src/pps57_sumo/generate_plain_corridor.py \
  --config configs/sumo_scenario_base.json \
  --output sumo/plain

netconvert \
  --node-files sumo/plain/corredor.nod.xml \
  --edge-files sumo/plain/corredor.edg.xml \
  --output-file sumo/network/corredor.net.xml \
  --no-turnarounds true \
  --tls.default-type static \
  --tls.cycle.time 90 \
  --tls.yellow.time 3
```

### Run SUMO Baseline

```bash
make run
make kpis
```

Or run the full baseline helper:

```bash
.venv/bin/python scripts/run_baseline.py
```

Generated outputs:

```text
outputs/tripinfo.xml
outputs/summary.xml
outputs/statistics.xml
reports/baseline_kpis.json
```

### Run Scenario Suite

The scenario suite is declared in `configs/sumo_scenario_base.json` and
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
statistics.

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

Baseline vs RL comparison table:

```bash
make compare-tsp-rl
```

This writes:

```text
reports/tsp_baseline_vs_rl_comparison.md
reports/tsp_baseline_vs_rl_comparison.json
```

Realistic demonstrator:

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
SUMO/TraCI artifacts generated by those runs.

Decision outcome evaluation:

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

In `--mode sumo`, runtime RL decisions receive instantaneous TraCI network
features per TLS: queue/halting counts, lane vehicle count, mean speed, waiting
time, occupancy, spillback risk and active TSP request count.

Event-log training dataset seed:

```bash
make build-event-training-dataset
```

This writes `outputs/event_training_dataset.jsonl` by joining C-ITS messages,
TSP decisions and actuation logs produced by SUMO/TraCI.
Run a TSP SUMO workflow first; an empty or missing event dataset is rejected by
policy optimization and RL training.

Generated outputs:

```text
outputs/tsp_decisions.jsonl
outputs/tsp_actuation.jsonl
reports/tsp_emulation_summary.json
```

### Run Offline Policy Optimization

```bash
.venv/bin/python scripts/run_policy_optimization.py
```

Or:

```bash
make optimize-offline
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
.venv/bin/python scripts/run_rl_training.py
```

Or:

```bash
make train-rl-policy
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

### Validate Platform Artifacts

```bash
.venv/bin/python scripts/check_platform_data.py
```

Useful options:

```bash
.venv/bin/python scripts/check_platform_data.py --strict
.venv/bin/python scripts/check_platform_data.py --max-records 1000
.venv/bin/python scripts/check_platform_data.py --out reports/platform_snapshot.json
```

Generated output:

```text
reports/platform_snapshot.json
```

### Run Scenario Dashboard

```bash
.venv/bin/python scripts/run_dashboard.py --host 127.0.0.1 --port 8000
```

The dashboard and local API are unauthenticated. Non-loopback hosts are blocked by
default; use `--allow-non-loopback` only behind an explicit network restriction.

With reload:

```bash
.venv/bin/python scripts/run_dashboard.py --reload
```

Dashboard endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/` | Scenario dashboard UI |
| `GET` | `/api/health` | API and runner status |
| `GET` | `/api/scenarios` | Configured scenario catalog |
| `GET` | `/api/runs/current` | Current or last managed scenario run |
| `POST` | `/api/runs/start` | Start a scenario run |
| `POST` | `/api/runs/stop` | Stop the active managed scenario run |
| `GET` | `/api/reports` | Latest per-scenario KPI reports |
| `GET` | `/api/reports/{scenario_id}` | KPI comparison for one scenario |

Start a full baseline / no-actuation / actuation comparison through the API:

```bash
curl -X POST http://127.0.0.1:8000/api/runs/start \
  -H 'Content-Type: application/json' \
  -d '{"scenario_id":"baseline_am_peak","run_type":"comparison","steps":7200,"traci_port":8813}'
```

Set `"gui": true` in the request, or enable `Abrir sumo-gui` in the dashboard,
to run the selected scenario visually with SUMO GUI.

Run every configured scenario with the same comparison:

```bash
curl -X POST http://127.0.0.1:8000/api/runs/start \
  -H 'Content-Type: application/json' \
  -d '{"all_scenarios":true,"run_type":"comparison","steps":7200,"traci_port":8813}'
```

### Inspect Platform Artifacts

```bash
make platform-check
```

The platform check reads generated artifacts and writes `reports/platform_snapshot.json` with:

- artifact availability;
- C-ITS message counts and recent events;
- TSP decisions and Safety Layer outcomes;
- actuation logs;
- policy candidate comparison;
- exported policy summaries;
- SUMO baseline KPIs.

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

The simulation interface contract is documented in `docs/protocol_icd.md`. It
separates standard-like fields, operator extensions, SUMO artifacts, synthetic
geometry, trust assumptions and lifecycle audit expectations.

Optional broker transport effects are configured in
`configs/cits_v2x_config.json` under `message_transport`. They can inject
seeded latency, jitter, loss, duplicate delivery and reordering while keeping
the default run ideal and deterministic.

## Message Examples

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
| `outputs/tripinfo.xml` | SUMO baseline | Per-vehicle SUMO trip information |
| `outputs/scenarios/<scenario>/<run_type>/seed_<seed>/` | Scenario runner | Per-scenario SUMO, C-ITS and TSP run artifacts |
| `reports/cits_emulation_summary.json` | C-ITS runs | Message counts and request summary |
| `reports/tsp_emulation_summary.json` | TSP runs | Decision, safety and actuation summary |
| `reports/protocol_lifecycle_audit.json` | `scripts/audit_protocol_lifecycle.py` | Replayed SREM/SSEM/TSP/actuation lifecycle audit |
| `reports/baseline_kpis.json` | KPI parser | SUMO baseline KPI summary |
| `reports/scenarios/<scenario>/<run_type>/seed_<seed>/kpis.json` | Scenario runner | Per-run KPI summary used by scenario reports |
| `reports/policy_report.json` | Policy optimization | Exported safe runtime policy |
| `reports/policy_optimization_summary.json` | Policy optimization | Offline comparison summary |
| `reports/tabular_q_policy_report.json` | RL training | Exported tabular Q-learning policy |
| `reports/rl_training_summary.json` | RL training | RL training metrics |
| `reports/platform_snapshot.json` | Platform check/API | Aggregated artifact snapshot |

## Safety Layer Rules

The Safety Layer is mandatory for baseline decisions, optimized runtime decisions and offline policy candidates. It blocks or downgrades decisions when safety conditions are not met, including:

- current yellow transition;
- unsafe phase sequence;
- minimum green not satisfied;
- maximum green extension exceeded;
- maximum total green exceeded;
- cooldown after previous priority intervention;
- maximum consecutive priority interventions per signal;
- expired or non-actuable requests.

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
| `configs/sumo_scenario_base.json` | Corridor geometry, demand, PT services, stops and SUMO generation inputs |
| `configs/cits_v2x_config.json` | OBU, RSU, C-ITS logging and safety constraints |
| `configs/tsp_safety_config.json` | TSP scoring, actuation and runtime policy settings |
| `configs/policy_training_config.json` | Candidate actions, reward and RL training settings |
| `configs/platform_config.json` | Platform artifact paths, labels and load limits |
| `configs/scenario_catalog.yaml` | Scenario descriptors |
| `configs/signal_policy_constraints.yaml` | Signal policy constraints |

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

### Platform checks have no data

`make platform-check` aggregates existing artifacts; it does not create SUMO,
C-ITS or TSP logs. Run a SUMO/TraCI workflow first:

```bash
make build
make tsp-sumo-no-actuation
make build-event-training-dataset
make platform-check
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

### Dashboard or API port is already in use

Start the dashboard on another port:

```bash
.venv/bin/python scripts/run_dashboard.py --port 8001
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
