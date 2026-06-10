# Demo — baseline vs TSP on the real Boavista corridor (the value proof)

This is the **value proof**: with everything else held identical on the real OSM
Boavista corridor (geometry, reference demand, real STCP buses), does turning the
TSP on actually make the buses faster — safely, and by a believable amount?

## What it runs

Two arms, same corridor, paired by bus (identical routes/departures):

- **baseline** — plain SUMO, no signal priority.
- **tsp** — a thin TraCI loop that, for each bus approaching a non-green traffic
  light, builds a priority request and runs the **real TSP decision engine + Safety
  Layer**; approved priority is actuated via TraCI (`setPhaseDuration`).

The loop reuses the same validated engine + Safety Layer as
`scripts/empirical_network_profile_check.py`. It processes only buses (fast) and
sets `signal_program_verified(True)` to bypass the global contract-verification
gate that the OSM net's *joined* intersections trip (they have no conflict matrix);
the Safety Layer's per-decision checks (min-green, yellow, clearance, cooldown,
max-consecutive) still run on every request.

## Reproduce

```bash
# after the reference corridor exists (scripts/build_reference_corridor.py)
.venv/bin/python scripts/run_tsp_demo.py --end 3600
```

## Result (`docs/validation/demo_baseline_vs_tsp.json`, 1-hour window)

| | |
|---|---|
| Buses paired | **20** |
| Mean bus **time-loss improvement** | **22.2 s**, 95% paired CI **[2.7, 41.7] s** |
| Significant (CI excludes zero) | **yes** |
| Mean **running-time improvement** | **4.15%** |
| Published envelope (TSP, US-DOT ITS 2009-b00613) | 2–18% → **within envelope** |
| TSP loop | 3 805 decisions → **28 approved / actuated**, 3 777 Safety-blocked |
| **Verdict** | **value_demonstrated** |

So the TSP cut bus time loss by ~22 s on average (significant), a **4.15%
running-time improvement that lands inside the published transit-signal-priority
envelope** — on the real Porto corridor, with the Safety Layer gating every
request (only safe interventions actuate).

The block reasons are honest and expected: most requests are blocked because the
bus is *too close for a safe red truncation*, the phase is *yellow*, the green
*window is already sufficient*, or the TLS is in *cooldown* — i.e. TSP intervenes
only when it is both useful and safe.

## Honest scope and limits

- **Magnitude is plausible, not calibrated.** The demand is HCM/Madrid-referenced
  (illustrative), not Porto-measured (V2/CMP), so the 4.15% is a *face-valid*
  number (it sits in the TCRP envelope), not a calibrated prediction. The rigorous,
  calibrated number belongs on the synthetic corridor (HCM-calibrated demand).
- **Signals are netconvert defaults here** (the Webster signals are V4d; using them
  would need them baked into the net so the engine's phase view matches).
- **Small sample** (20 buses in 1 h): the CI is significant but modest; a longer run
  tightens it.
- The thin loop is the demo's actuation path; the full C-ITS message pipeline
  (`TSPControlController`) is validated separately and is too slow at this scale.

## Next

- The same demo on the **synthetic corridor** (calibrated demand) for the rigorous,
  statistically-tight number — the two arms of the agreed demo (real = credibility,
  synthetic = rigour).
- Fold in the **Webster signals** (V4d) and longer windows; wire the 3 demo scenarios
  (delay / restraint+block / bunching) through this runner.
