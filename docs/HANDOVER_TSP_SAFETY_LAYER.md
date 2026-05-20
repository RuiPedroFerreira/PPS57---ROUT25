# Handover TSP Safety Layer — Implementado

O TSP Safety Layer foi implementado como motor de decisão TSP com Safety Layer e atuação dry-run/TraCI.

## Implementado

- `src/pps57_tsp/engine.py`: motor de decisão multiobjetivo.
- `src/pps57_tsp/safety.py`: validação de segurança antes da atuação.
- `src/pps57_tsp/actuator.py`: atuador dry-run e atuador TraCI.
- `src/pps57_tsp/controller.py`: integração C-ITS -> TSP -> Safety -> Atuação.
- `scripts/run_tsp_control.py`: entrada CLI para dry-run e SUMO.
- `configs/tsp_config.json`: pesos, thresholds, actuation policy e phase mapping proxy.
- `tests/test_tsp_safety_layer.py`: testes unitários do motor e da safety layer.

## Fluxo resultante

```text
SREM-like recebido pela RSU
        ↓
Validação C-ITS C-ITS/V2X emulation
        ↓
Motor TSP TSP Safety Layer
        ↓
Safety Layer
        ↓
Dry-run ou TraCI setPhaseDuration
        ↓
Logs e resumo
```

## Próximo handover

Consultar `docs/HANDOVER_POLICY_OPTIMIZATION.md` para evolução para otimização avançada e RL offline.
