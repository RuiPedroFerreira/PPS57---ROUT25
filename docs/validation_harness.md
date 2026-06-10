# Sim-to-Real Validation Harness (V0)

This is the **measuring instrument** for the real-world validation plan — the
independent ruler that breaks the circularity where SUMO is both the
system-under-test and its own oracle. It ships **no data and assumes nothing**:
it consumes `(modelled, observed)` pairs that *you* supply from real sources, and
every threshold it applies is traceable to a published standard.

## Why this exists

The platform's KPIs are currently measured against SUMO's own synthetic outputs
(`calibration_status: "scaffolding_pending_data"` in `configs/sumo_scenario_base.json`).
Real validation means comparing each layer of the simulation against an
*independent* source of truth. Before gathering that data, we need an
acceptance instrument with fixed, falsifiable criteria — otherwise any result
can be rationalised as "good". This module is that instrument.

## What it computes (and the source of every number)

All thresholds live in [`configs/validation_config.json`](../configs/validation_config.json),
each under a `source` field. The code reads them; there are no magic numbers.

| Gate | Criterion | Source |
|---|---|---|
| GEH per link | `GEH = √[2(M−C)²/(M+C)]`; < 5 good, 5–10 investigate, > 10 problem | DMRB; FHWA Traffic Analysis Toolbox Vol. III |
| Network calibration | GEH < 5 for **≥ 85%** of links | DMRB / FHWA TAT Vol. III (Wisconsin DOT table) |
| Link-flow band | within 100 veh/h (<700), 15% (700–2700), 400 veh/h (>2700) | FHWA TAT Vol. III (Wisconsin DOT) |
| Sum of flows | within 5% of counts **and** GEH < 4 | FHWA TAT Vol. III (Wisconsin DOT) |
| Travel time | within 15% (or 1 min, if higher) for ≥ 85% of cases | FHWA TAT Vol. III (Wisconsin DOT) |
| TSP face validity | bus running-time gain in **2–18%**; delay reduction in **15–80%** | US-DOT ITS Benefits DB (2009-b00613); TCRP Synthesis 83/149 |

## Module layout

```text
src/pps57_sumo/validation/metrics.py      Pure formulas: GEH, RMSE, %RMSE, Pearson, band checks
src/pps57_sumo/validation/acceptance.py   Config-driven gates; verdicts echo each threshold's source
scripts/run_validation.py                 CLI; requires real input files, refuses to invent data
configs/validation_config.json            Thresholds, each with a traceable `source`
tests/test_validation_metrics.py          Formula-verification fixtures (the math is the source of truth)
```

It reuses the existing Student-t CI machinery in `src/pps57_sumo/stats.py` for the
absolute-percentage-error confidence interval — one implementation, one t-table.

## How to use it

The harness takes JSON lists of pairs you assemble from real observations. It
will not run on nothing.

```bash
.venv/bin/python scripts/run_validation.py \
  --link-flows path/to/modelled_vs_observed_flows.json \
  --travel-times path/to/modelled_vs_observed_times.json \
  --tsp-face-validity path/to/measured_tsp_gains.json \
  --out reports/validation/model_validation.json
```

Input shapes (each row should carry its own `source`):

```json
// --link-flows
[{"link_id": "I2_I3", "modelled_veh_h": 870, "observed_veh_h": 812, "source": "CMP detector, 2026-..."}]
// --travel-times
[{"segment_id": "casa_musica->serralves", "modelled_s": 540, "observed_s": 505, "source": "STCP AVL, ..."}]
// --tsp-face-validity
[{"metric": "bus_running_time_improvement_pct", "value_pct": 9.4, "source": "scenario delayed_bus_westbound"}]
```

## Where this sits in the plan

This PR is **V0 only — the instrument**. The measurements come next, each
behind its own independent source of truth, in follow-up PRs:

- **V1** — port the TSP engine onto a reference SUMO scenario (MoST/LuST) via the
  existing `scripts/empirical_network_profile_check.py`; benchmark the policy
  layer on RESCO/sumo-rl.
- **V2** — calibrate demand from real counts (CMP/IMT); validate flows here with GEH.
- **V3** — regenerate public transport from the STCP GTFS feed (`gtfs_source`).
- **V4** — import real geometry (OSM Porto/Boavista) and signal plans.
- **V5** — validate outcomes against STCP AVL travel times and the TSP face-validity envelope.

## Sources

- GEH statistic — DMRB (Design Manual for Roads and Bridges)
- [FHWA Traffic Analysis Toolbox Volume III](https://ops.fhwa.dot.gov/trafficanalysistools/tat_vol3/sect5.htm) (Wisconsin DOT calibration targets)
- [US-DOT ITS Benefits Database — TSP 2–18%](https://www.itskrs.its.dot.gov/2009-b00613)
- TCRP Synthesis 83 / 149 — Transit Signal Priority
