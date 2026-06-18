# `legacy_porto/` — arquivo da exploração sim-to-real de Porto/Boavista

Estes scripts pertencem a uma **fase anterior** do projeto, em que a validação
"sim-to-real" foi tentada construindo um cenário real do **corredor da Avenida da
Boavista (Porto)** a partir de fontes abertas: extrato OSM, GTFS da STCP e um
envelope de contagens europeias de referência (a chamada *ladder* V2/V3/V4).

## Porque é que está arquivado

O projeto pivotou para o **cenário real e calibrado de Ingolstadt** (TUM-VT
`sumo_ingolstadt`, Apache-2.0). Esse cenário **já vem pronto** — rede
`ingolstadt_net.net.xml`, procura motorizada calibrada por detetores
(`routes_<dia>_24h_det_calib`), programas semafóricos reais (`TL/`) e transporte
público real via GTFS da INVG. Obtém-se com um `git clone` e corre-se com
`scripts/run_ingolstadt_demo.py`.

Ou seja, **toda esta pipeline de construção deixou de ter análogo**: não há nada
para descarregar/netconvert/snapping de paragens quando a rede já está
construída e calibrada por terceiros. Manter estes scripts no caminho principal
seria enganador. Foram movidos para aqui (e não apagados) para preservar o
método e o histórico — continuam recuperáveis e auditáveis.

## O que está aqui

| Script | O que fazia |
|---|---|
| `fetch_boavista_osm.py` | Download pinado (SHA-256) do extrato OSM de Boavista via Overpass. |
| `build_boavista_network.py` | `netconvert` do extrato OSM → `boavista.net.xml`. |
| `fetch_stcp_gtfs.py` | Download pinado do GTFS da STCP (Porto Open Data, CC0). |
| `build_stcp_pt_on_boavista.py` | Projeção das paragens/linhas STCP (500/502/204) na rede OSM. |
| `build_reference_corridor.py` | Procura de fundo sintética (randomTrips) calibrada à banda de Madrid + sinais Webster. |
| `fetch_reference_counts.py` | Contagens reais de Madrid (informo) + UK DfT, com proveniência/SHA. |
| `run_v2_demand_validation.py` | Verifica a intensidade arterial modelada contra o envelope europeu (face-validity, não calibração). |
| `run_tsp_demo.py` | Demo de valor baseline-vs-TSP no corredor OSM de Boavista (gémeo Porto de `run_ingolstadt_demo.py`). |

## Estado

- **Não mantidos** para Ingolstadt. Continuam a funcionar de forma autónoma
  (recalculam `ROOT` para a raiz do repo), mas dependem de descarregar dados de
  Porto/Madrid que podem ter mudado a montante.
- A capacidade **reutilizável** (matriz de conflitos autoritativa, perfil de rede
  empírico, parsers GTFS e de contagens) ficou no caminho vivo, em
  `src/pps57_sumo/{network_binding,network_profile}.py` e
  `src/pps57_sumo/validation/{gtfs_pt,reference_counts,metrics,acceptance}.py`.
- A cobertura de regressão destes scripts vive em
  `tests/test_legacy_porto_evidence.py`.

## Equivalente atual

Para validação em rede real, usa o caminho de Ingolstadt:

```bash
git clone --depth 1 https://github.com/TUM-VT/sumo_ingolstadt.git .tools/ingolstadt
python scripts/run_ingolstadt_demo.py --steps 300
python scripts/run_network_binding_check.py        # matriz de conflitos na net de Ingolstadt
python scripts/empirical_network_profile_check.py --network .tools/ingol_run/ingolstadt_net.net.xml
```
