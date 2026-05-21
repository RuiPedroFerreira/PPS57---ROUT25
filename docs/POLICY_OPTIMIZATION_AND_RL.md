# Policy Optimization and Reinforcement Learning

## Objetivo

Policy Optimization adds offline policy comparison, runtime inference, and tabular Q-learning over the TSP Safety Layer. The explainable TSP engine remains the baseline, and the Safety Layer is mandatory for every candidate decision.

```text
Cenários offline
        ↓
Baseline TSP decision engine
        ↓
Candidatos de política otimizada
        ↓
Safety Layer obrigatória
        ↓
Exported safe runtime policy
        ↓
TSP runtime inference
        ↓
Safety Layer obrigatória
```

O treino fica sempre fora do TSP Decision Engine. O engine/controller apenas
carrega um artefacto exportado (`rules`) para inferência runtime; não atualiza
pesos, Q-table ou reward durante a execução operacional.

## Implementação

Localização:

```text
src/pps57_opt/
```

Componentes:

- `dataset.py`: contém fixtures de testes unitários; não é usado como fonte operacional.
- `event_dataset.py`: transforma logs C-ITS/TSP gerados por SUMO/TraCI em cenários de treino/otimização.
- `optimizer.py`: evaluates candidate actions, computes the reward, and exports the best safe action per state.
- `policy_runtime.py`: loads an exported policy and proposes decisions during semi-live TSP runs.
- `rl_trainer.py`: trains a tabular Q-learning policy on SUMO/TraCI event-derived scenarios.
- `state.py`: partilha o bucketing de estado entre treino offline e inferência runtime.
- `models.py`: define amostras offline, avaliações de candidatos e regras exportadas.
- `config.py`: carrega `configs/policy_optimization_config.json`.

## Safety Layer obrigatória

Cada candidato é convertido numa `TSPDecision` e validado por `TSPSafetyLayer`. Se a safety layer devolver `blocked_by_safety`, o candidato fica registado no log, mas não pode ser escolhido pela política otimizada.

Isto evita que uma política offline proponha ações como:

- extensão de verde fora da fase verde do movimento prioritário;
- truncagem durante amarelo;
- early green sem sequência de fase válida;
- violação de limites de verde, cooldown ou intervenções consecutivas.

## Como executar

```bash
python scripts/build_event_training_dataset.py
make optimize-offline
make train-rl-policy
python scripts/run_tsp_control.py --mode sumo --policy-mode optimized --policy-report reports/policy_report.json
python scripts/run_tsp_control.py --mode sumo --policy-mode rl
python scripts/compare_tsp_baseline_rl.py --train-rl
python scripts/evaluate_decision_outcomes.py --train-rl
```

Outputs:

```text
outputs/offline_policy_samples.jsonl
outputs/policy_candidates.jsonl
reports/policy_report.json
reports/policy_optimization_summary.json
reports/tabular_q_policy_report.json
reports/rl_training_summary.json
reports/tsp_baseline_vs_rl_comparison.md
reports/decision_outcome_evaluation.md
outputs/event_training_dataset.jsonl
```

## Melhorias para avaliação RL

O treino operacional usa cenários derivados dos logs SUMO/TraCI. Quando os logs
incluem pressão de tráfego, múltiplos pedidos simultâneos e KPIs de rede, esses
campos entram no estado e podem favorecer `reevaluate_next_cycle` em vez de
`green_extension`.

O `state_bucket` passou a incluir:

- fase/ETA/delay/tempo até mudança de fase;
- pressão de tráfego (`active_request_count`, fila, veículos parados, velocidade média, waiting time, ocupação e risco de spillback);
- estado temporal de intervenção quando disponível.

No runtime SUMO/TraCI, o controller passa à política um `NetworkStateSnapshot`
com KPIs instantâneos lidos via TraCI para cada TLS:

- `vehicle_count`;
- `queue_vehicle_count`;
- `halted_vehicle_count`;
- `mean_speed_mps`;
- `waiting_time_s`;
- `occupancy`;
- `spillback_risk`;
- `active_request_count`.

Esses campos são observáveis no momento da decisão e podem ser usados pela
política RL.

## Outcome evaluator

`scripts/evaluate_decision_outcomes.py` compara baseline vs RL decisão a
decisão, usando como chave estável `timestamp_s + vehicle_id + tls_id` porque
`request_id` é UUID e muda entre runs.

Verdicts:

- `same`: resultado observável igual ao baseline;
- `safer_or_less_intrusive`: RL reduz bloqueios/atuações ou evita uma ação mais intrusiva;
- `unsafe_or_blocked`: proposta RL foi bloqueada quando o baseline não foi;
- `inconclusive`: a decisão mudou, mas faltam KPIs para provar impacto;
- `inconclusive_without_kpis`: verdict de rede quando não há KPIs SUMO emparelhados.

O evaluator não usa o reward como prova. Sem `baseline_kpis` e `rl_kpis`, ele
não declara melhoria de rede.

## Limitações

The RL implementation is intentionally conservative: it is tabular Q-learning over event-derived SUMO/TraCI scenarios, not production online learning. Runtime mode performs online inference from an exported policy, then still passes every proposal through the Safety Layer before actuation.

For event-trained RL in a later phase, the same boundary should be kept:

```text
event logs -> offline feature/reward dataset -> RL training -> exported policy report -> TSP runtime inference
```

The Decision Engine should consume only the exported policy report, with fallback
to `baseline_tsp_decision_engine` when no runtime rule exists.

Para comparação com KPIs SUMO reais/calibrados:

```bash
python scripts/compare_sumo_kpis.py \
  --baseline-kpis reports/baseline_kpis.json \
  --rl-kpis reports/rl_kpis.json
```
