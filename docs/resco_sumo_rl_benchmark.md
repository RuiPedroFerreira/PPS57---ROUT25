
# RESCO / sumo-rl benchmark — scoping and reference method port

This documents an honest attempt to "benchmark against RESCO/sumo-rl". The
headline finding is a **scoping result**: a direct leaderboard comparison would
be apples-to-oranges, because this project's learning layer solves a *different
task* from the one RESCO benchmarks. What we can do soundly — and did — is port
the TSP pipeline onto a RESCO reference network and report it honestly.

## Why a direct RL leaderboard comparison is not apt

| | This project | RESCO / sumo-rl |
|---|---|---|
| Task | **TSP action selection** for a transit vehicle | **Signal-phase selection** to minimise network delay |
| Trigger | a bus priority request (SREM) | every control step, always |
| Action space | `green_extension`, `early_green`, `reject`, `no_action`, `reevaluate` | choose next phase index |
| Reward | schedule-delay / headway / proximity, Safety-gated | network waiting time / queue / pressure |
| Learner | tabular **contextual bandit** (ε-greedy) | DQN / PPO / MPLight + Fixed-Time, Max-Pressure, Max-Wave baselines |

This is not a guess about our own code — `src/pps57_opt/rl_trainer.py` states it in
its own *"Nota de honestidade"*: with `gamma = 0` and cached per-`(scenario, action)`
rewards there is no `s → s'` transition, so it is **a tabular contextual bandit
with ε-greedy exploration**, not temporal-difference Q-learning. Its state
(`src/pps57_opt/state.py`) is built *from a priority request* (ETA, schedule
delay, movement phase, traffic pressure) — without a transit request there is no
state at all.

RESCO scenarios are general car traffic with no transit line, no RSU/OBU C-ITS,
and a phase-selection action space. Scoring our request-conditioned TSP bandit on
RESCO's network-delay leaderboard would compare two different systems on a task
only one of them is built for. We therefore do **not** report such a score.

## What we did instead — method port onto a RESCO reference network

We reuse the V1 approach (`docs/v1_most_reference_validation.md`, added in the V1 PR):
run the TSP engine + Safety Layer on a real RESCO network and check that the
map-agnostic `NetworkProfile` faithfully reproduces it.

### Source of truth

[`cologne8`](https://github.com/LucasAlegre/sumo-rl) — an 8-intersection slice of
the real Cologne network, shipped in the MIT-licensed sumo-rl package (the
`cologne8` **data** is Creative Commons; TAPASCologne/OSM lineage).
**Downloaded, not vendored** by `scripts/fetch_resco_cologne.py`.

| | |
|---|---|
| Repo | `github.com/LucasAlegre/sumo-rl` |
| Tag / commit | `v1.4.5` / `f2d1d871ca79febb9a60b2b64fec2f9c6470e129` |
| File | `sumo_rl/nets/RESCO/cologne8/cologne8.net.xml` (354,765 bytes) |
| SHA-256 | `51194519fd4d69726612d38b44606637c31389b86079f909139ba8f1aaffd2a8` |
| Licenses | sumo-rl package MIT; cologne8 data Creative Commons |

### Reproduce

```bash
.venv/bin/python scripts/fetch_resco_cologne.py
.venv/bin/python scripts/run_reference_network_check.py \
  --net .tools/resco-cologne8/cologne8.net.xml \
  --label RESCO_cologne8_reference_method_port \
  --out docs/validation/cologne8_method_check.json
```

### Findings (`docs/validation/cologne8_method_check.json`)

- **7 / 8 traffic lights** profiled and probed end-to-end; the 8th honestly
  recorded as `no_reachable_movement` (no target phase reachable through an
  intergreen from the probe's current phase).
- **0 profile mismatches** on every probed TLS — the map-agnostic `NetworkProfile`
  reproduces SUMO's loaded TLS programs exactly on the real Cologne network.
- The Safety Layer **approved** the `early_green` interventions here
  (`approved_red_truncation`) — in contrast to MoST, where the probe timing hit
  unsafe windows and all were blocked. Across the two reference networks we now
  have **both** gate outcomes (approve and fail-closed block) demonstrated on real
  geometry, neither tuned for.

**Verdict: pass** — zero profile mismatches and end-to-end execution on a second,
independent real reference network (RESCO lineage).

## What a real RESCO comparison would require (future work)

1. A **TSC adapter** exposing the project's controller as a phase-selector over
   RESCO's action/observation space (a different control surface than the TSP
   action set).
2. **Injecting transit demand + RSU/OBU C-ITS** into the RESCO scenario so the TSP
   layer has something to prioritise.
3. A network-delay reward and a run against RESCO's Fixed-Time / Max-Pressure /
   Max-Wave baselines.

That is effectively a different system and is out of scope for the SUMO-only TSP
platform; it is recorded here as future work, not claimed as done.

## Sources

- [sumo-rl (MIT)](https://github.com/LucasAlegre/sumo-rl) — bundles the RESCO nets
- [RESCO (GPL-3.0)](https://github.com/Pi-Star-Lab/RESCO) — Fixed-Time / Max-Pressure / Max-Wave baselines, real Cologne/Luxembourg/Salt Lake City scenarios
