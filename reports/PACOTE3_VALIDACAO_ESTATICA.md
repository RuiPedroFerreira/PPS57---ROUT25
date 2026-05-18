# Pacote 3 — Validação estática e dry-run

## Comandos executados

```bash
make validate
make test
make cits-dryrun
```

## Resultado

```text
OK XML: sumo/plain/corredor.nod.xml
OK XML: sumo/plain/corredor.edg.xml
OK XML: sumo/routes/routes.rou.xml
OK XML: sumo/additional/bus_stops.add.xml
OK XML: sumo/additional/detectors.add.xml
OK XML: sumo/corredor.sumocfg
Static validation completed. Runtime validation with netconvert/sumo is still required.

Ran 7 tests in 0.013s
OK

Resumo da emulação C-ITS:
- total_messages: 40
- by_type: {'MAPEM_like': 7, 'SPATEM_like': 7, 'SREM_like': 13, 'SSEM_like': 13}
- unique_request_ids: 13
- unique_vehicle_ids: 3
- unique_rsu_ids: 7
- acknowledged_messages: 3
- rejected_messages: 10
- mode: dry-run
- steps: 60
- scenario_id: porto_boavista_realista_v02_pacote3_cits
```

## Observação

A validação com SUMO/TraCI continua dependente de ambiente local com SUMO instalado e rede compilada com `netconvert`.
