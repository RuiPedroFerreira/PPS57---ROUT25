# Handover para o Pacote 4 — Motor de decisão TSP

O Pacote 3 deixou pronta a camada de mensagens C-ITS/V2X. O Pacote 4 deve ligar as respostas aceites da RSU a um motor de decisão semafórica seguro.

## Entradas disponíveis

- `SREM_like`: pedido de prioridade com veículo, linha, ETA, atraso, distância e interseção.
- `SPATEM_like`: estado semafórico observado via TraCI ou snapshot estático.
- `MAPEM_like`: aproximações e movimentos prioritários da interseção.
- `configs/cits_config.json`: mapeamento RSU/TLS/interseção.
- `configs/signal_policy_constraints.yaml`: restrições semafóricas criadas no Pacote 2.

## Saídas esperadas no Pacote 4

- Decisão TSP auditável:
  - `no_action`;
  - `green_extension_5s`;
  - `green_extension_10s`;
  - `early_green_if_safe`;
  - `reject_with_reason`;
  - `reevaluate_next_cycle`.

- Camada de segurança:
  - nunca violar verde mínimo;
  - nunca saltar amarelo/all-red;
  - respeitar clearance pedonal;
  - aplicar cooldown;
  - limitar extensão máxima;
  - registar motivo de cada decisão.

- Adaptador TraCI de atuação:
  - ler fase atual;
  - calcular tempo restante;
  - aplicar `setPhaseDuration` apenas após validação;
  - evitar `setRedYellowGreenState` direto no MVP, salvo em testes controlados.

## Próxima arquitetura

```text
SREM-like recebido pela RSU
        ↓
Validação C-ITS Pacote 3
        ↓
Motor TSP Pacote 4
        ↓
Safety Layer
        ↓
SUMO TraCI Adapter
        ↓
KPIs e relatório
```
