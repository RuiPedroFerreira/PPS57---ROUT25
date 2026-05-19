# Handover do Pacote 5 — Otimização Avançada e RL Offline

O Pacote 5 implementa a primeira camada offline de otimização sobre o baseline operacional explicável do Pacote 4:

```text
Cenários offline -> baseline TSP -> candidatos otimizados -> Safety Layer -> política segura exportada
```

## Componentes implementados

- Configuração: `configs/optimization_config.json`.
- Dataset offline sintético: `src/pps57_opt/dataset.py`.
- Treino/avaliação tabular proxy: `src/pps57_opt/optimizer.py`.
- Script de execução: `scripts/run_pacote5_optimization.py`.
- Testes: `tests/test_pacote5_optimization.py`.
- Documentação: `docs/PACOTE5_OTIMIZACAO_RL_OFFLINE.md`.

## Como executar

```bash
make optimize-offline
```

Outputs:

- `outputs/pacote5_offline_samples.jsonl`;
- `outputs/pacote5_policy_candidates.jsonl`;
- `reports/pacote5_policy_report.json`;
- `reports/pacote5_optimization_summary.json`.

## Garantias atuais

- O baseline TSP do Pacote 4 é sempre avaliado.
- Candidatos otimizados são comparados contra o baseline.
- A Safety Layer é chamada para todos os candidatos.
- Candidatos `blocked_by_safety` ficam nos logs, mas não podem ser selecionados.
- Guardrails do baseline são preservados para `no_action`, `reevaluate_next_cycle` e `reject`.

## Próximas linhas de evolução

1. Substituir cenários sintéticos por logs SUMO reais/calibrados:
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
