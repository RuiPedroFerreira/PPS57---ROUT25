# Results

## Evaluation setup

The platform was evaluated on the full catalogue of eight operational scenarios. Each
scenario was run as a **paired comparison** between the `baseline` arm (the TSP controller
in dry-run, taking decisions but never actuating) and the `tsp_actuation` arm (identical,
but applying the approved commands), over **three matched random seeds (17, 42, 57)** — a
total of 48 two-hour SUMO runs. Because both arms share demand, geometry, and seed, every
measured difference is attributable to the actuation itself rather than to the execution
machinery. Results are reported as the paired mean difference across seeds, with a 95 %
confidence interval (Student's *t*, two degrees of freedom) and a paired *t*-test.

## Public-transport impact

Across the catalogue, the actuated arm shows a **directionally consistent reduction in bus
delay**: seven of the eight scenarios improve the mean bus time loss, and the gain is
reflected in door-to-door bus travel time (duration) as well.

| Scenario | Bus time-loss Δ | Bus travel-time Δ |
|---|---:|---:|
| Congested morning peak | −59.5 s (−21.4 %) | −67.6 s (−6.8 %) |
| Congested delayed bus | −40.5 s (−14.7 %) | −47.6 s (−4.7 %) |
| Off-peak | −26.6 s (−11.7 %) | −32.5 s (−3.4 %) |
| Morning peak | −24.5 s (−9.5 %) | −29.9 s (−3.0 %) |
| Cross-traffic pressure | −24.3 s (−8.7 %) | −27.4 s (−2.7 %) |
| Emergency-vehicle conflict | −14.6 s (−5.5 %) | −17.9 s (−1.8 %) |
| Delayed westbound bus | −13.1 s (−4.8 %) | −18.1 s (−1.8 %) |
| Bunched buses | +0.3 s (+0.1 %) | −2.6 s (−0.3 %) |

Two qualifications are essential to reading this table honestly.

First, **the bus improvement is a genuine travel-time gain, not a metering artefact**. Buses
are released into the network on schedule (their mean departure delay is ≈ 0.3 s in both
arms), so the consistent reduction in bus *duration* reflects faster progression through the
corridor rather than vehicles being held back at the boundary. The departure-delay confound
that can distort general-traffic figures does not apply to the buses.

Second, and decisively, **none of these improvements reaches statistical significance at the
95 % level with three seeds**. The seed-to-seed variance is large — the confidence intervals
are wider than the point estimates in almost every case. The strongest case, the congested
morning peak, is borderline (*t* = −4.0 against a critical value of 4.30) but still does not
clear the threshold. In other words, the results establish a **consistent and plausible
direction of benefit, but not a proven effect size**. A single-seed reading would have
reported, for the morning peak, a confident "−9.5 % bus delay"; the three-seed analysis
reveals that figure to carry a ±17 % interval and to be statistically indistinguishable from
zero. This is the central methodological finding: **conclusions about TSP benefit require
replication, and three seeds are not yet enough to support a significance claim**.

## General-traffic impact

The effect on general traffic is **not demonstrable** in either direction. Across scenarios
the mean general-traffic time loss ranges from −6.1 % to +5.1 %, and every confidence
interval includes zero. The honest conclusion is that, at this demand and with this number of
replications, **the TSP strategy imposes no detectable systematic penalty on general
traffic — and confers no detectable benefit either**. Any apparent cross-traffic improvement
seen in a single run is within the noise band.

## Operational behaviour of the decision engine

Beyond the aggregate KPIs, the per-decision logs confirm that the engine behaves as a
**selective, cost-aware, safety-bounded controller** rather than an indiscriminate priority
dispenser. Of the 800–1000 decisions taken per scenario (across the three seeds), only a
minority result in applied actuation; the large majority are deferrals or reasoned
rejections. The dominant decision outcomes are:

- **minimum-green protection** (`early_green_deferred_until_min_green_served`) — the most
  frequent reason in every scenario;
- **cost-awareness** (`intervention_benefit_too_small`) — priority withheld when the expected
  saving is below threshold;
- **clearance-phase protection** (`early_green_precheck_defer`) and **safe red-truncation**
  (`bus_too_close_for_safe_red_truncation`);
- the **need and score gates** (`priority_score_below_threshold`);
- **congestion awareness** (`network_pressure_defer_intervention: spillback_risk`).

Two of these confirm the scenarios behaved as designed. The spillback-risk deferral and the
safe red-truncation refusal both occur **most frequently in the cross-traffic-pressure and
congested scenarios** — precisely the conditions under which the engine is expected to back
off. The Safety Layer is therefore active and measurable; notably, it manifests as **upstream
deferral** (shaping the request before it becomes unsafe) rather than as post-approval vetoes,
so no hard safety block was recorded even though safety constraints visibly governed hundreds
of decisions.

## Two scenario-specific findings

**Bunched buses.** This scenario applied *more* priority actuations than any other (87
bus-priority actions plus 121 green-compensation actions across the three seeds) yet produced
**no net bus benefit (+0.1 %)**. The engine intervenes heavily but to no effect. The cause is
in the decision logic: the need gate and the priority score key on the *magnitude* of headway
deviation, not its sign, so a bus running too close to its leader (a negative deviation) is
treated as needing priority just like a bus that has fallen behind. The bunching scenario thus
exposes a real limitation — the engine does not currently suppress priority for a closely
following bus, it merely wastes effort on it. This is the clearest candidate for a targeted
improvement to the need gate.

**Emergency-vehicle conflict.** Emergency requests are processed and actuated (15–28 per
seed), bypassing the conditional need and score gates as designed and resulting in early-green
and green-extension actions to cover the emergency vehicle's approach. The hierarchy operates
within the safety envelope. One diagnostic note: some emergency decisions carry the reason
`early_green_current_phase_signal_group_unknown`, indicating an incomplete signal-group
mapping in this scenario that degrades — without disabling — the emergency response, and which
warrants separate investigation.

## Summary

The multi-seed evaluation supports a measured set of claims. The platform demonstrates a
**complete, selective, and safety-bounded TSP chain**, and produces a **consistent directional
reduction in bus delay** that is a real travel-time gain rather than a metering artefact,
**without any measurable penalty to general traffic**. It does **not**, on the present
evidence, support a statistically significant effect size: three seeds are insufficient, and
the confidence intervals remain wide. To convert the consistent direction into a defensible
quantitative result, the evaluation should be extended to roughly **eight to twelve seeds per
scenario** — the congested cases are close to significance and would likely cross it first,
while the low-effect scenarios (delayed bus, emergency) will require the larger sample.
