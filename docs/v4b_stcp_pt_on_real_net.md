# V4b — real STCP corridor PT on the real Boavista network

V4b puts the **real public transport on the real geometry**: it maps the real STCP
stops of the corridor lines (500, 502 and now **204**) onto the real OSM-derived
Boavista network, emits SUMO bus stops on real lanes, regenerates the PT services
with the **real (V3) headways**, and validates that SUMO loads them. It builds on
V4 (geometry) and V3 (headways).

## Source of truth — and no new dependencies

- Stops, coordinates, directions and headways: the **real STCP GTFS** (CC0; V3).
- Edges: the **real OSM Boavista net** (ODbL; V4).
- Stop → edge projection: an **affine map from the net's own `<location>`
  metadata** (UTM zone 29) plus sumolib nearest-edge snapping. Validated accurate
  to a **median ~6 m** snap distance.

`gtfs2pt` (the standard SUMO importer) is deliberately **not** used: it requires
`rtree` + `pandas` (heavy native deps) that would pollute the locked `.venv`. The
mapping here uses only `sumolib`, which already ships with SUMO.

## Reproduce

```bash
# prerequisites: V3 GTFS + V4 net already fetched/built into .tools/
.venv/bin/python scripts/build_stcp_pt_on_boavista.py
```

Outputs the bus-stop additional into the git-ignored `.tools/` and the validation
report into `docs/validation/v4b_stcp_pt_mapping.json`.

## Findings (`docs/validation/v4b_stcp_pt_mapping.json`)

- All three corridor lines instantiated, **line 204 added** (it runs the corridor
  but was missing from the synthetic config).
- **53 / 69** in-bbox stops snapped to a real bus-capable edge, **median 6.1 m**.
  The 16 that did not snap are **all boundary-clipping losses** (roads cut by the
  corridor bbox, e.g. Jardim Botânico, Mercado da Foz, Lordelo on fringe streets) —
  **0 interior snapping gaps**.
- Services carry the **real weekday GTFS headways** per line/direction (e.g. 204
  ~12 min AM, 500 ~11–13 min, 502 ~13 min).
- **SUMO loads the generated bus stops** on the real net without error.

**Verdict: pass** — every corridor line (incl. 204) instantiated, SUMO loads the
stops, and every non-snapped stop is an expected boundary-clipping loss (zero
interior gaps).

## Honest scope and limits

- The projection is an **affine approximation** from the net's location box (no
  pyproj/PROJ); validated at ~6 m median, adequate for stop snapping.
- Only the **corridor segment inside the net bbox** is instantiated; stops beyond
  it are dropped (the lines extend well past Boavista).
- **Signal timings remain synthetic** (V4 limit — real plans are not open).
- This emits bus **stops + a regenerated services structure**; wiring full bus
  **flows/routes** into a runnable SUMO scenario (via `duarouter`) and measuring KPIs
  is the remaining step before V5.

## Next

- Route the bus flows (duarouter) and run the corridor with real PT + real headways.
- **V5** — validate observed travel times against the STCP real-time AVL feed (NGSI, CC0).
