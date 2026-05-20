# Validação estática - SUMO Digital Twin v0.2

Resultado da validação estática executada neste ambiente:

```text
OK XML: sumo/plain/corredor.nod.xml
OK XML: sumo/plain/corredor.edg.xml
OK XML: sumo/routes/routes.rou.xml
OK XML: sumo/additional/bus_stops.add.xml
OK XML: sumo/additional/detectors.add.xml
OK XML: sumo/corredor.sumocfg
Static validation completed. Runtime validation with netconvert/sumo is still required.
```

Validação dinâmica pendente:

- `netconvert` para gerar `sumo/network/corredor.net.xml`;
- `sumo` para correr `sumo/corredor.sumocfg`;
- `sumo-gui` para inspeção visual;
- `parse_tripinfo.py` para gerar KPIs baseline.

Motivo: o ambiente atual não tem SUMO instalado.
