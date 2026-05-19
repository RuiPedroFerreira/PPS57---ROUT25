# Handover para o Pacote 5 — Otimização Avançada e RL

O Pacote 4 deixa implementado um baseline operacional explicável:

```text
SREM-like aceite -> decisão TSP -> Safety Layer -> dry-run/TraCI actuation
```

## Entradas disponíveis para o Pacote 5

- Logs C-ITS: `outputs/cits_messages.jsonl`.
- Logs de decisão: `outputs/tsp_decisions.jsonl`.
- Logs de atuação: `outputs/tsp_actuation.jsonl`.
- Resumo TSP: `reports/tsp_emulation_summary.json`.
- Configuração TSP: `configs/tsp_config.json`.
- Safety constraints: `configs/cits_config.json` e `configs/signal_policy_constraints.yaml`.

## Próximas linhas de evolução

1. Criar cenários comparativos:
   - sem prioridade;
   - TSP green extension apenas;
   - TSP green extension + early green;
   - TSP com pesos multiobjetivo diferentes.

2. Calcular KPIs antes/depois:
   - atraso médio dos autocarros;
   - número de paragens em vermelho;
   - tempo de viagem dos autocarros;
   - atraso do tráfego geral;
   - filas por aproximação;
   - emissões.

3. Preparar ambiente RL/MARL:
   - estado: fase, filas, ETA, atraso, headway, pedidos ativos;
   - ações: no action, green extension, early green, reject, reevaluate;
   - reward: redução de atraso do TP menos impacto no tráfego geral e penalizações de segurança.

4. Manter a Safety Layer como filtro obrigatório para qualquer política aprendida.
