# Policy Optimization Handover

O Policy Optimization implementa a primeira camada offline de otimização sobre o baseline operacional explicável do TSP Safety Layer:

```text
SUMO/TraCI event logs -> event dataset -> baseline TSP -> candidatos otimizados -> Safety Layer -> política segura exportada
```

## Componentes implementados

- Configuração: `configs/policy_optimization_config.json`.
- Dataset a partir de eventos SUMO/TraCI: `src/pps57_opt/event_dataset.py`.
- Fixtures unitárias de cenários: `src/pps57_opt/dataset.py`.
- Offline policy comparison: `src/pps57_opt/optimizer.py`.
- Runtime inference: `src/pps57_opt/policy_runtime.py`.
- Tabular Q-learning: `src/pps57_opt/rl_trainer.py`.
- Estado treino/runtime partilhado: `src/pps57_opt/state.py`.
- KPIs instantâneos SUMO/TraCI para decisão: `TraciSimulationAdapter.read_network_state()`.
- Execution scripts: `scripts/run_policy_optimization.py`, `scripts/run_rl_training.py`.
- Testes: `tests/test_policy_optimization.py`.
- Documentação: `docs/POLICY_OPTIMIZATION_AND_RL.md`.

## Como executar

```bash
python scripts/build_event_training_dataset.py
make optimize-offline
make train-rl-policy
python scripts/run_tsp_control.py --mode sumo --policy-mode rl
python scripts/compare_tsp_baseline_rl.py --train-rl
python scripts/evaluate_decision_outcomes.py --train-rl
```

Outputs:

- `outputs/offline_policy_samples.jsonl`;
- `outputs/policy_candidates.jsonl`;
- `reports/policy_report.json`;
- `reports/policy_optimization_summary.json`.
- `reports/tabular_q_policy_report.json`;
- `reports/rl_training_summary.json`.

## Garantias atuais

- O baseline TSP do TSP Safety Layer é sempre avaliado.
- Candidatos otimizados são comparados contra o baseline.
- A Safety Layer é chamada para todos os candidatos.
- Candidatos `blocked_by_safety` ficam nos logs, mas não podem ser selecionados.
- Guardrails do baseline são preservados para `no_action`, `reevaluate_next_cycle` e `reject`.
- O modo runtime `rl` carrega apenas a política exportada (`reports/tabular_q_policy_report.json` por defeito); o treino não corre dentro do TSP Decision Engine.
- O treino operacional usa `outputs/event_training_dataset.jsonl`, derivado de logs SUMO/TraCI.
- O relatório `reports/tsp_baseline_vs_rl_comparison.md` mostra deltas entre baseline e RL.
- O relatório `reports/decision_outcome_evaluation.md` classifica decisão a decisão, mantendo impacto de rede como inconclusivo quando não há KPIs SUMO emparelhados.
- Em `--mode sumo`, a política runtime recebe `NetworkStateSnapshot` real via TraCI.

## Próximas linhas de evolução

1. Enriquecer os logs SUMO reais/calibrados:
   - sem prioridade;
   - TSP green extension apenas;
   - TSP green extension + early green;
   - TSP com pesos multiobjetivo diferentes.

2. Calcular KPIs antes/depois com dados operacionais:
   - atraso médio dos autocarros;
   - número de paragens em vermelho;
   - tempo de viagem dos autocarros;
   - atraso do tráfego geral;
   - filas por aproximação;
   - emissões.

3. Evoluir o ambiente RL/MARL:
   - estado: fase, filas, ETA, atraso, headway, pedidos ativos;
   - ações: no action, green extension, early green, reject, reevaluate;
   - reward: redução de atraso do TP menos impacto no tráfego geral e penalizações de segurança.

4. Manter a Safety Layer como filtro obrigatório para qualquer política aprendida antes de qualquer atuação TraCI.
