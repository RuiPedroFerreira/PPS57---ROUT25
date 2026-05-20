# TSP Safety Layer — Validação estática e dry-run

Foram executados os comandos:

```bash
make validate
make test
make tsp-dryrun
```

Resultado resumido:

```text
OK XML: sumo/plain/corredor.nod.xml
OK XML: sumo/plain/corredor.edg.xml
OK XML: sumo/routes/routes.rou.xml
OK XML: sumo/additional/bus_stops.add.xml
OK XML: sumo/additional/detectors.add.xml
OK XML: sumo/corredor.sumocfg
Static validation completed. Runtime validation with netconvert/sumo is still required.

Ran 13 tests in 0.029s
OK

Resumo do controlo TSP:
- total_decisions: 3
- by_action: {'green_extension': 1, 'no_action': 1, 'early_green': 1}
- by_status: {'approved': 2, 'not_actuable': 1}
- approved_decisions: 2
- blocked_by_safety: 0
- green_extension_decisions: 1
- early_green_decisions: 1
- no_action_decisions: 1
- actuation_events: 3
- applied_events: 2
- dry_run_applied_events: 2
- real_traci_applied_events: 0
- cits_acknowledged_messages: 3
- cits_rejected_messages: 10
```

A execução dinâmica com SUMO/TraCI continua dependente de uma instalação local de SUMO e de rede compilada com `netconvert`.
