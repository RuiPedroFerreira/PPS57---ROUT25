# PPS57 ROUT25 Traffic Priority Platform

PPS57 ROUT25 is a local validation platform for public-transport traffic-signal priority on a realistic Porto/Boavista corridor model. It combines a SUMO digital twin, C-ITS/V2X message emulation, an explainable TSP decision engine, a mandatory Safety Layer, offline policy optimization, tabular reinforcement-learning training, and a FastAPI control plane.

The repository is designed for technical demonstration and validation. It is not yet an operationally calibrated traffic-control deployment: network geometry, demand, public-transport lines and signal plans are proxy assets that must be replaced or calibrated with real OSM, GTFS, traffic-count and signal-controller data before any production use.

## What It Does

- Builds and runs a SUMO corridor scenario for Porto/Boavista.
- Emulates C-ITS/V2X messages in JSON form: `MAPEM_like`, `SPATEM_like`, `SREM_like`, and `SSEM_like`.
- Generates bus priority requests from emulated OBUs and validates them through RSUs.
- Converts accepted priority requests into TSP decisions.
- Applies a Safety Layer before any signal actuation.
- Supports SUMO/TraCI actuation and SUMO/TraCI no-actuation observation mode.
- Exports JSONL logs and JSON reports for auditability.
- Compares offline policy candidates against the baseline TSP engine.
- Trains a tabular Q-learning policy from SUMO/TraCI event-derived scenarios.
- Loads exported policies for runtime inference in semi-live TSP runs.
- Serves a local FastAPI control plane for demos and artifact inspection.

## Repository Layout

```text
configs/                 Runtime configuration for C-ITS, TSP, policy training and platform checks
docs/                    Technical documentation
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

## Requirements

Use Python 3.11+ or 3.12. The local environment used by this project is `.venv`.

For full simulation support:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

For a reproducible install with exact pinned versions (releases, CI, dependency
regression triage), use the lockfile instead:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

`requirements.txt` declares flexible version ranges; `requirements.lock`
captures the full transitive closure at exact versions. Regenerate the lockfile
after changing `requirements.txt` with `pip freeze > requirements.lock`.

Full SUMO execution also requires SUMO CLI tools in `PATH`:

```text
netconvert
sumo
sumo-gui
```

## Quick Start

These commands validate the project and run non-simulation utilities. Operational C-ITS/TSP data is produced only by SUMO/TraCI runs:

```bash
make validate
make test
make build
make cits-sumo
make tsp-sumo-no-actuation
make build-event-training-dataset
make optimize-offline
make train-rl-policy
make platform-check
```

Start the local API:

```bash
make platform-api
```

Default URLs:

```text
FastAPI:   http://127.0.0.1:8000
API docs:  http://127.0.0.1:8000/docs
```

## Available Make Targets

| Target | Purpose | SUMO required |
|---|---|---|
| `make validate` | Validate project structure and JSON configs | No |
| `make test` | Run all unit tests | No |
| `make build` | Generate SUMO plain files and compile the network | Yes |
| `make run` | Build and run the SUMO baseline | Yes |
| `make gui` | Build and open the SUMO GUI baseline | Yes |
| `make kpis` | Parse `outputs/tripinfo.xml` into `reports/baseline_kpis.json` | Needs tripinfo |
| `make cits-sumo` | Run C-ITS emulation connected to SUMO/TraCI | Yes |
| `make tsp-sumo` | Run TSP with SUMO and TraCI actuation | Yes |
| `make tsp-sumo-no-actuation` | Run TSP with SUMO observation only | Yes |
| `make tsp-gui` | Run TSP with SUMO GUI and TraCI actuation | Yes |
| `make tsp-gui-no-actuation` | Run TSP with SUMO GUI observation only | Yes |
| `make optimize-offline` | Compare safe offline policy candidates | No |
| `make train-rl-policy` | Train tabular Q-learning policy offline | No |
| `make platform-check` | Aggregate and validate platform artifacts | No |
| `make platform-api` | Start the FastAPI control API | No |
| `make sort-routes` | Sort SUMO route definitions by departure time | No |
| `make clean` | Remove known generated artifacts | No |

## Command-Line Workflows

### 1. Static Validation

```bash
make validate
```

Checks the repository structure and validates the main JSON configs:

```text
configs/cits_config.json
configs/tsp_config.json
configs/policy_optimization_config.json
configs/platform_config.json
```

### 2. Run Tests

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

### 3. Build SUMO Network

```bash
make build
```

Direct equivalent:

```bash
.venv/bin/python src/pps57_sumo/generate_plain_corridor.py \
  --config configs/corridor_config.json \
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

### 4. Run SUMO Baseline

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

### 5. Run C-ITS/V2X Emulation

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

### 6. Run TSP Control With Safety Layer

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

Generated outputs:

```text
outputs/tsp_decisions.jsonl
outputs/tsp_actuation.jsonl
reports/tsp_emulation_summary.json
```

### 7. Run Offline Policy Optimization

```bash
.venv/bin/python scripts/run_policy_optimization.py
```

Or:

```bash
make optimize-offline
```

What it does:

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

### 8. Train Tabular Reinforcement Learning Policy

```bash
.venv/bin/python scripts/run_rl_training.py
```

Or:

```bash
make train-rl-policy
```

What it does:

- Runs tabular Q-learning over SUMO/TraCI event-derived scenarios.
- Keeps production online learning disabled.
- Exports a policy report that can be inspected or loaded for runtime inference.
- Keeps the Safety Layer as a mandatory guardrail.

Generated outputs:

```text
reports/tabular_q_policy_report.json
reports/rl_training_summary.json
```

### 9. Validate Platform Artifacts

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

### 10. Run FastAPI Control Plane

```bash
.venv/bin/python scripts/run_platform_api.py --host 127.0.0.1 --port 8000
```

With reload:

```bash
.venv/bin/python scripts/run_platform_api.py --reload
```

Core endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | API and runner status |
| `GET` | `/runs/current` | Current or last managed run |
| `POST` | `/runs/start` | Start a supported job |
| `POST` | `/runs/stop` | Stop the active managed job |
| `POST` | `/runs/pause` | Pause the active managed job, where supported |
| `POST` | `/runs/resume` | Resume a paused managed job, where supported |
| `GET` | `/artifacts/snapshot` | Aggregated artifact snapshot |
| `GET` | `/events/recent` | Recent JSONL events for one artifact |

Start a TSP SUMO/TraCI observation run through the API:

```bash
curl -X POST http://127.0.0.1:8000/runs/start \
  -H 'Content-Type: application/json' \
  -d '{"kind":"tsp-sumo-no-actuation","steps":7200,"policy_mode":"baseline"}'
```

Start optimized runtime inference through the API:

```bash
curl -X POST http://127.0.0.1:8000/runs/start \
  -H 'Content-Type: application/json' \
  -d '{"kind":"tsp-sumo-no-actuation","steps":7200,"policy_mode":"optimized","policy_report":"reports/policy_report.json"}'
```

Start RL training through the API:

```bash
curl -X POST http://127.0.0.1:8000/runs/start \
  -H 'Content-Type: application/json' \
  -d '{"kind":"train-rl-policy"}'
```

Supported API job kinds:

```text
cits-sumo
tsp-sumo
tsp-sumo-no-actuation
optimize-offline
train-rl-policy
platform-check
```

### 11. Inspect Platform Artifacts

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
MAPEM_like: RSU publishes intersection topology
SPATEM_like: RSU publishes signal state
SREM_like: OBU requests priority
SSEM_like: RSU acknowledges, rejects or forwards the request to the TSP engine
```

Messages are JSON/Python-native for functional validation. They are not ASN.1/UPER ETSI encodings.

## Message Examples

### MAPEM_like

Describes the topology of one controlled intersection.

```json
{
  "message_type": "MAPEM_like",
  "protocol_version": "0.3.0",
  "source_id": "RSU_BOAVISTA_02",
  "destination_id": "BROADCAST",
  "timestamp_s": 0.0,
  "message_id": "mapem-demo-001",
  "intersection_id": "I2",
  "tls_id": "I2",
  "rsu_id": "RSU_BOAVISTA_02",
  "intersection_name": "Boavista I2",
  "approaches": [
    {
      "approach_id": "I1_I2",
      "edge_id": "I1_I2",
      "direction": "westbound",
      "is_priority_corridor": true,
      "lane_ids": ["I1_I2_0"]
    }
  ]
}
```

### SPATEM_like

Describes the current signal phase and remaining timing context.

```json
{
  "message_type": "SPATEM_like",
  "protocol_version": "0.3.0",
  "source_id": "RSU_BOAVISTA_02",
  "destination_id": "BROADCAST",
  "timestamp_s": 100.0,
  "message_id": "spatem-demo-001",
  "intersection_id": "I2",
  "tls_id": "I2",
  "current_phase_index": 0,
  "current_program_id": "static",
  "red_yellow_green_state": "GGrr",
  "next_switch_s": 132.0,
  "spent_duration_s": 18.0,
  "controlled_lanes": ["I1_I2_0", "I3_I2_0"]
}
```

### SREM_like

Represents a priority request sent by an emulated OBU.

```json
{
  "message_type": "SREM_like",
  "protocol_version": "0.3.0",
  "source_id": "OBU_bus_I2",
  "destination_id": "RSU_BOAVISTA_02",
  "timestamp_s": 100.0,
  "message_id": "srem-demo-001",
  "request_id": "request-demo-001",
  "vehicle_id": "bus_I2",
  "vehicle_class": "bus",
  "line_id": "STCP500_PROXY_W",
  "route_id": "route_boavista_proxy",
  "intersection_id": "I2",
  "tls_id": "I2",
  "rsu_id": "RSU_BOAVISTA_02",
  "current_edge_id": "I1_I2",
  "current_lane_id": "I1_I2_0",
  "speed_mps": 10.0,
  "distance_to_stopline_m": 160.0,
  "eta_to_stopline_s": 16.0,
  "schedule_delay_s": 120.0,
  "headway_deviation_s": 0.0,
  "requested_maneuver": "green_extension",
  "priority_level": "public_transport_high_delay",
  "expires_at_s": 130.0,
  "status": "requested"
}
```

### SSEM_like

Represents the RSU response to an `SREM_like` request.

```json
{
  "message_type": "SSEM_like",
  "protocol_version": "0.3.0",
  "source_id": "RSU_BOAVISTA_02",
  "destination_id": "OBU_bus_I2",
  "timestamp_s": 100.2,
  "message_id": "ssem-demo-001",
  "correlation_id": "srem-demo-001",
  "request_id": "request-demo-001",
  "vehicle_id": "bus_I2",
  "intersection_id": "I2",
  "tls_id": "I2",
  "rsu_id": "RSU_BOAVISTA_02",
  "status": "acknowledged",
  "action": "forward_to_decision_engine",
  "reason": "accepted_for_tsp_decision_engine",
  "valid_until_s": 115.2,
  "confidence": 1.0,
  "safety_notes": [
    "Request accepted by RSU for TSP evaluation.",
    "Signal actuation must pass through the TSP Safety Layer."
  ]
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
| `reports/cits_emulation_summary.json` | C-ITS runs | Message counts and request summary |
| `reports/tsp_emulation_summary.json` | TSP runs | Decision, safety and actuation summary |
| `reports/baseline_kpis.json` | KPI parser | SUMO baseline KPI summary |
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
| `configs/corridor_config.json` | Corridor geometry and SUMO generation inputs |
| `configs/cits_config.json` | OBU, RSU, C-ITS logging and safety constraints |
| `configs/tsp_config.json` | TSP scoring, actuation and runtime policy settings |
| `configs/policy_optimization_config.json` | Candidate actions, reward and RL training settings |
| `configs/platform_config.json` | Platform artifact paths, labels and load limits |
| `configs/scenarios.yaml` | Scenario descriptors |
| `configs/signal_policy_constraints.yaml` | Signal policy constraints |
| `configs/calibration_targets.yaml` | Calibration target placeholders |

## Development Checks

Before sharing changes:

```bash
make validate
make test
.venv/bin/python -m pytest -q
git diff --check
```

## Troubleshooting

If `sumo` or `netconvert` is missing, install SUMO and ensure the binaries are in `PATH`.

If platform artifact checks have no data, run a SUMO/TraCI workflow first:

```bash
make build
make tsp-sumo-no-actuation
make build-event-training-dataset
make platform-check
```

To control runs, start the API in another terminal:

```bash
make platform-api
```

If port `8000` is already in use, run the script directly with a different port:

```bash
.venv/bin/python scripts/run_platform_api.py --port 8001
```

If an optimized runtime policy is not loaded, regenerate the policy report:

```bash
make optimize-offline
.venv/bin/python scripts/run_tsp_control.py --mode sumo --policy-mode optimized --policy-report reports/policy_report.json
```
