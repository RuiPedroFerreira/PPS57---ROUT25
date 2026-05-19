# Handover Pacote 4 — Implementado

O Pacote 4 foi implementado como motor de decisão TSP com Safety Layer e atuação dry-run/TraCI.

## Implementado

- `src/pps57_tsp/engine.py`: motor de decisão multiobjetivo.
- `src/pps57_tsp/safety.py`: validação de segurança antes da atuação.
- `src/pps57_tsp/actuator.py`: atuador dry-run e atuador TraCI.
- `src/pps57_tsp/controller.py`: integração C-ITS -> TSP -> Safety -> Atuação.
- `scripts/run_tsp_control.py`: entrada CLI para dry-run e SUMO.
- `configs/tsp_config.json`: pesos, thresholds, actuation policy e phase mapping proxy.
- `tests/test_pacote4_tsp.py`: testes unitários do motor e da safety layer.

## Fluxo resultante

```text
SREM-like recebido pela RSU
        ↓
Validação C-ITS Pacote 3
        ↓
Motor TSP Pacote 4
        ↓
Safety Layer
        ↓
Dry-run ou TraCI setPhaseDuration
        ↓
Logs e resumo
```

## Próximo handover

Consultar `docs/HANDOVER_PACOTE5.md` para evolução para otimização avançada e RL offline.
