# V4d — reference-grade demand + Webster signals on the real corridor

V4d upgrades the two synthetic layers of the real Boavista corridor (background
demand, signal timings) from *illustrative* to **standards/reference grade**, so
the corridor is a credible stage for the TSP demo — not arbitrary.

## Source of truth

**Signal timings — Webster (1958).** `C_opt = (1.5·L + 5) / (1 − ΣYᵢ)` with green
splits ∝ critical flow ratios, computed by SUMO `tlsCycleAdaptation.py` from the
actual routed demand. Replaces netconvert defaults. (Per-intersection; corridor
coordination would be `tlsCoordinator.py`.)

**Background demand — HCM, validated against Madrid.** An explicit arterial
through-flow per direction (HCM-anchored ~700 veh/h, cf. the project's
HCM-derived ~1224 veh/h inbound = ~612 veh/h/lane) plus diffuse cross-traffic
(SUMO `randomTrips`). Validated against measured intensities from
[Madrid Open Data](https://datos.madrid.es/) (median 397, P75 819, P90 1329 veh/h
per detector — [PMC11416623](https://pmc.ncbi.nlm.nih.gov/articles/PMC11416623/)).

## Reproduce

```bash
# after V4 build + V4b mapping
.venv/bin/python scripts/build_reference_corridor.py
```

## Findings (`docs/validation/v4d_reference_corridor.json`)

- **Arterial intensity p90 = 819.5 veh/h** (median 707) — lands **exactly on
  Madrid's P75 (819)** and inside the measured band → `arterial_p90_in_madrid_band: true`.
- **Webster optimised all 60 traffic-light programs** from the routed demand.
- The corridor runs: **4,464 vehicles** complete (mean speed ~14.7 m/s) and **46
  STCP buses** traverse, mean trip ~525 s, **time loss ~90 s** — lower than V4c's
  ~161 s under netconvert-default signals, i.e. Webster timing already helps the
  buses before any TSP.

**Verdict: pass** — arterial demand in the Madrid band, Webster signals applied,
corridor runs with cars + real buses.

## Honest scope and limits

- Still a **transfer/adaptation**, not Porto-measured: the *magnitudes* are HCM and
  Madrid-validated, the *OD pattern* is synthetic (randomTrips), and the signals
  are Webster-optimal **for this demand**, not the actual CMP plans.
- Webster is per-intersection (no green-wave coordination here).
- Stops are affine-snapped (V4b, ~6 m median).

## Reproducibility note

The repo directory name `PPS57---ROUT25` contains `---`, which is **illegal inside
an XML comment**. SUMO tools echo their (absolute) file paths into XML comments, so
passing absolute paths made the tools emit/!read malformed XML (randomTrips and
tlsCycleAdaptation both failed cryptically). The builder therefore runs every SUMO
tool with `cwd=ROOT` and **relative** paths, and strips comments when parsing tool
output. Keep this if the repo is ever renamed/moved.

## Next

The **demo runner**: baseline vs TSP on this credible corridor (the value "money
shot"), with the gain sanity-checked against the V0 TCRP envelope; then V5 vs the
STCP AVL feed.
