# V4 — real Boavista geometry from OpenStreetMap

V4 replaces the synthetic 5-intersection corridor with the **real Avenida da
Boavista road network** from OpenStreetMap, and validates that the TSP engine +
Safety Layer run on it. This is the first time the platform's geometry is real
Porto, not a plausible stand-in.

## Source of truth

OpenStreetMap (ODbL), fetched via the Overpass API and converted with netconvert.
**Downloaded/built, not vendored** — the OSM extract and net live in the
git-ignored `.tools/boavista-osm/`; only our derived report is committed.

| | |
|---|---|
| OSM bbox (S,W,N,E) | `41.156, -8.692, 41.170, -8.627` — from Nominatim-sourced corridor endpoints (Rotunda da Boavista → Castelo do Queijo) + margin |
| OSM extract SHA-256 | `012ac5593ce72b131944ad368a41bb48f517387fb4c62d136045ff9a9cffce99` |
| netconvert | Eclipse SUMO 1.26.0 (`osmNetconvert.typ.xml`, documented options) |
| net SHA-256 | `fd604bc5beb4bd3a300a4e8e0a141ddbb69577d1fe7f507981e548f3709020a6` |
| License | ODbL (OpenStreetMap contributors) |

OSM is a live database; the SHA-256 pins the exact snapshot the committed report
was produced from. The `NetworkProfile` fingerprint equals the net SHA-256.

## Reproduce

```bash
.venv/bin/python scripts/fetch_boavista_osm.py        # Overpass -> .tools/boavista-osm/boavista.osm.xml
.venv/bin/python scripts/build_boavista_network.py    # netconvert -> boavista.net.xml
.venv/bin/python scripts/run_reference_network_check.py \
  --net .tools/boavista-osm/boavista.net.xml \
  --label V4_boavista_osm_real_geometry_method_port \
  --out docs/validation/v4_boavista_osm_method_check.json
```

## Findings (`docs/validation/v4_boavista_osm_method_check.json`)

The real network is far larger than the synthetic corridor: **60 traffic-light
programs and ~2,095 edges** for the Boavista district (vs 5 synthetic
intersections).

- **35 / 60 traffic lights** probed end-to-end through the TSP engine + Safety
  Layer; the other 25 honestly recorded `no_reachable_movement` (no target phase
  reachable through an intergreen from the probe's current phase).
- **0 profile mismatches** on every probed TLS — the map-agnostic `NetworkProfile`
  reproduces SUMO's loaded TLS programs exactly on the real OSM-derived network.
- The Safety Layer engaged on every probe: **31 approved** (e.g.
  `approved_red_truncation`) and **4 blocked** (fail-closed). Both gate outcomes
  on real Porto geometry, neither tuned.

**Verdict: pass** — zero profile mismatches and end-to-end execution on the real
Boavista geometry.

## Honest scope and limits

- **Signal timings are not real.** OSM provides traffic-signal *locations*
  (`highway=traffic_signals`); netconvert synthesises the *programs*. Real STCP/CMP
  signal plans are not openly available (confirmed in the data probe), so the TLS
  timings here are netconvert defaults — real locations, synthetic timings.
- The bbox covers the **Boavista district**, broader than the avenue alone, so the
  60 TLS include cross streets. That is real geometry, just wider than the
  synthetic corridor's axis.
- 25/60 TLS had no probe-reachable movement at the fixed probe time — a property of
  the single-shot probe, not a defect.

## Public transport regeneration — next step (V4b), not done here

Both prerequisites now exist: **real geometry** (this PR) and **real STCP headways**
(V3). Regenerating `public_transport.services` onto the real network — mapping the
real STCP stops for lines 500/502/204 to the real edges (via SUMO `gtfs2pt`),
applying the V3 headways, and adding line 204 — is the immediate follow-up. It is
**deliberately not bundled here**: `gtfs2pt` onto a 60-TLS network with the full
STCP feed is its own pipeline that must be validated on its own, and shipping it
unverified would overstate what is checked. V5 then validates outcomes against the
STCP real-time AVL feed.
