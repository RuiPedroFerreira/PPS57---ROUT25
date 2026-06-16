# TSP Baseline vs RL Comparison

| Metric | Baseline | RL | Delta RL-Baseline |
|---|---:|---:|---:|
| total_decisions | 17 | 17 | 0 |
| cits_acknowledged_messages | 17 | 17 | 0 |
| actuation_events | 17 | 17 | 0 |
| applied_events | 1 | 1 | 0 |
| blocked_by_safety | 0 | 2 | 2 |
| action:early_green | 1 | 3 | 2 |
| action:no_action | 0 | 2 | 2 |
| action:reevaluate_next_cycle | 15 | 11 | -4 |
| action:reject | 1 | 1 | 0 |
| status:approved | 1 | 1 | 0 |
| status:blocked_by_safety | 0 | 2 | 2 |
| status:not_actuable | 16 | 14 | -2 |
| runtime_policy_loaded | false | true |  |
| rl_policy_id |  | tabular_q_learning_policy |  |
| rl_algorithm |  | tabular_q_learning |  |
| rl_rule_count |  | 9 |  |
| rl_policy_source |  | /Users/ruipedro/Desktop/PPS57---ROUT25/reports/tabular_q_policy_report.json |  |
