# V3 — public transport validated against the real STCP GTFS

V3 replaces the *proxy* public-transport assumptions with facts from the **real
STCP timetable**. The scenario config is honest that its PT is a stand-in
(`gtfs_source: status "planned"`, headways/dwells "proxy plausível"); this phase
checks those proxies against the real feed and surfaces the real numbers a later
regeneration (V4) will use.

## Source of truth

STCP GTFS feed, published **CC0** on the [Porto Open Data portal](https://opendata.porto.digital).
**Downloaded, not vendored** by `scripts/fetch_stcp_gtfs.py`.

| | |
|---|---|
| Dataset | `5275c986-592c-43f5-8f87-aabbd4e4f3a4` ("Horários, paragens e rotas da STCP") |
| Resource | `c71a52cd-…` — snapshot **05-06-2026** ("Mais Recente") |
| SHA-256 | `48870fdaf236e473173d1c6a576f305220fb723de6e6f7850eea8821ef5c0669` |
| License | Creative Commons CC0 |

## Reproduce

```bash
.venv/bin/python scripts/fetch_stcp_gtfs.py
.venv/bin/python scripts/run_stcp_gtfs_validation.py
```

The extractor (`src/pps57_sumo/validation/gtfs_pt.py`) streams `stop_times.txt`
filtered to the corridor lines and derives real weekday headways from trip
dispatch times (`service_id = "DIAS UTEIS"`).

## Findings (`docs/validation/v3_stcp_gtfs_check.json`)

All three corridor lines named by the config exist in the real feed: **500**
(Praça Liberdade–Matosinhos), **502** (Bolhão–Matosinhos), **204** (Hospital
S.João–Foz).

| Line | Real AM-peak headway (weekday) | Proxy densest headway | Verdict |
|---|---|---|---|
| 500 | ~12 min | 8 min | **proxy denser than real** (ratio 0.67) |
| 502 | ~13 min | 15 min | **realistic** (ratio 1.15) |

- The proxy headways are the **right order of magnitude**. Line 500's densest
  proxy window (a bus every 8 min) is more frequent than the real AM peak
  (~12 min); line 502's 15-min proxy matches reality (and the real midday).
- **Dwell is not encoded in this GTFS** — every `stop_times` row has
  `departure_time == arrival_time` (0/13872 target rows differ). So the proxy
  dwell (~20 s) **cannot be validated from GTFS**; it would need AVL or surveyed
  dwell data.
- **Line 204** runs the corridor (~11–12 min) but is **not** in
  `public_transport.lines` — a candidate to add when geometry is real (V4).

**Verdict: pass** — every modelled line's proxy headway sits within an
order-of-magnitude sanity band (0.5×–2×) of the real value. That band is a sanity
check, **not** a published calibration standard.

## Honest scope and limits

- V3 validates **headway realism only**. It does **not** rewrite
  `public_transport.services`: mapping real STCP stops to the synthetic corridor
  edges (`I1_I2`, …) requires real geometry, which is **V4**. The real numbers
  here are what V4 will regenerate from.
- The proxy services use sim-relative seconds with no stated wall-clock anchor, so
  the densest proxy window is compared to the real AM peak.
- Dwell and exact per-direction (W/E ↔ `direction_id`) mapping are left to V4/V5
  (the latter can use the STCP real-time AVL feed for observed travel times).

## Next

- **V4** — real OSM geometry; then regenerate `public_transport.services` from
  these real GTFS headways and add line 204.
- **V5** — observed travel times from the STCP real-time AVL feed (NGSI, CC0).
