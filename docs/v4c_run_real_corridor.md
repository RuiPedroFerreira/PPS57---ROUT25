# V4c — running the real Boavista corridor with real STCP buses

V4c closes the geometry+PT chain into a **runnable scenario**: it routes the real
STCP bus flows (lines 500/502/204, both directions) on the real OSM Boavista
network and runs SUMO, producing bus KPIs from a real run. Builds on V4b (stop
mapping), V4 (geometry) and V3 (headways).

## What it does

1. Reads the V4b stop→edge mapping and the real V3 headways.
2. Builds one SUMO bus `flow` per line+direction, visiting the real bus stops.
3. Routes them with **duarouter** on the real net.
4. Runs **SUMO** and parses bus KPIs with the project's `parse_tripinfo`.

## Reproduce

```bash
# after V4b (scripts/build_stcp_pt_on_boavista.py) has produced the bus stops
.venv/bin/python scripts/run_boavista_corridor.py
```

## Findings (`docs/validation/v4c_corridor_run.json`)

- **6 flows** (500/502/204 × 2 directions) → **51 bus vehicles routed** by
  duarouter → **46 completed** within the 2 h window.
- Bus KPIs from the real run:
  - mean trip duration **~595 s** over the corridor segment,
  - mean speed **~5.08 m/s ≈ 18 km/h** — a realistic urban bus commercial speed
    (face validity ✓),
  - mean time loss **~161 s**, mean **~5.8 stops**, mean waiting **~102 s**.
- Observed dispatch headways match the real input (e.g. line 204 = **12.0 min**,
  the real GTFS value; 500/502 ≈ 6 min combining both ~12-min directions).

**Verdict: pass** — flows route, SUMO runs, real buses complete the real corridor
with realistic speeds.

## Honest scope and limits

- **Buses only, no background traffic.** Open Porto vehicle counts do not exist
  (V2 is blocked), so adding general demand would be fabrication. The KPIs are
  therefore close to **free-flow** bus times — the time loss (~161 s) comes from
  signal stops and the 15 s dwells, **not** from real congestion.
- **Signal timings are synthetic** (netconvert defaults; real plans are not open —
  V4 limit), so the signal-induced delay is indicative, not calibrated.
- Stops are affine-snapped to the nearest real bus edge (V4b, ~6 m median); a few
  fringe stops were clipped by the corridor bbox.

## What this establishes

The platform now runs the **real corridor** (real geometry + real stops + real PT
lines + real headways) end-to-end in SUMO — the synthetic corridor is fully
replaced for public transport. The remaining gap to a calibrated digital twin is
**real background demand** (needs CMP/IMT counts, V2) and **real signal plans**
(needs CMP).

## Next

**V5** — compare these simulated bus travel times against the **STCP real-time AVL
feed** (NGSI, CC0) to validate them against observed reality.
