# Handover para C-ITS/V2X emulation - Emulação C-ITS/V2X

## Cenário disponível

O SUMO Digital Twin v0.2 disponibiliza um corredor realista Porto/Boavista com sete interseções semaforizadas e duas linhas de autocarro proxy. Cada interseção tem um RSU ID no ficheiro `configs/corridor_config.json`.

## Objetos que o C-ITS/V2X emulation deve consumir

- Interseções: `I1` a `I7`
- RSUs: `RSU_BOAVISTA_01` a `RSU_BOAVISTA_07`
- Linhas de autocarro: `STCP500_PROXY_*`, `STCP502_PROXY_*`
- Paragens: `bs_*`
- Detetores: `e1_*` e `e2_*`
- Regras de pedido OBU: `configs/signal_policy_constraints.yaml`

## Primeiro MVP do C-ITS/V2X emulation

1. Ligar ao SUMO via TraCI.
2. Identificar veículos `bus_*` em simulação.
3. Calcular distância e ETA até à próxima interseção.
4. Criar mensagem SREM-like quando o autocarro estiver a menos de 250 m e atrasado.
5. Entregar pedido à RSU correspondente.
6. Registar resposta SSEM-like ainda sem alterar o semáforo.
7. Só depois ativar ações TSP no TSP Safety Layer.

## IDs recomendados para mensagens

- OBU: `OBU_<vehicle_id>`
- RSU: valores `rsu_id` do config
- Interseção: `I1`...`I7`
- Pedido: `SREM_<sim_time>_<vehicle_id>_<intersection_id>`
- Resposta: `SSEM_<request_id>`

## Resultado esperado

Um log auditável de pedidos de prioridade, incluindo autocarro, linha, posição, ETA, atraso simulado, interseção destino, decisão preliminar e motivo.
