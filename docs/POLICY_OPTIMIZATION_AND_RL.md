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
```

## Implementação

Localização:

```text
src/pps57_opt/
```

Componentes:

- `dataset.py`: gera cenários offline sintéticos para green extension, no action, early green, reevaluate, reject e bloqueios de safety.
- `optimizer.py`: evaluates candidate actions, computes the reward, and exports the best safe action per state.
- `policy_runtime.py`: loads an exported policy and proposes decisions during semi-live TSP runs.
- `rl_trainer.py`: trains a tabular Q-learning policy on simulated offline scenarios.
- `models.py`: define amostras offline, avaliações de candidatos e regras exportadas.
- `config.py`: carrega `configs/policy_optimization_config.json`.

## Safety Layer obrigatória

Cada candidato é convertido numa `TSPDecision` e validado por `TSPSafetyLayer`. Se a safety layer devolver `blocked_by_safety`, o candidato fica registado no log, mas não pode ser escolhido pela política otimizada.

Isto evita que uma política offline proponha ações como:

- extensão de verde fora da fase verde do corredor;
- truncagem durante amarelo;
- early green sem sequência de fase válida;
- violação de limites de verde, cooldown ou intervenções consecutivas.

## Como executar

```bash
make optimize-offline
make train-rl-policy
python scripts/run_tsp_control.py --mode dry-run --policy-mode optimized --policy-report reports/policy_report.json
```

Outputs:

```text
outputs/offline_policy_samples.jsonl
outputs/policy_candidates.jsonl
reports/policy_report.json
reports/policy_optimization_summary.json
reports/tabular_q_policy_report.json
reports/rl_training_summary.json
```

## Limitações

The RL implementation is intentionally conservative: it is tabular Q-learning over simulated offline scenarios, not production online learning. Runtime mode performs online inference from an exported policy, then still passes every proposal through the Safety Layer before actuation.
