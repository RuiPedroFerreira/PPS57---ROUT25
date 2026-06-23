# `legacy_porto/` — arquivo da exploração sim-to-real de Porto/Boavista

Estes scripts pertencem a uma **fase anterior** do projeto, em que a validação
"sim-to-real" foi tentada construindo um cenário real do **corredor da Avenida da
Boavista (Porto)** a partir de fontes abertas: extrato OSM, GTFS da STCP e um
envelope de contagens europeias de referência (a chamada *ladder* V2/V3/V4).

## Porque é que está arquivado

O projeto deixou de tentar *construir* um cenário real a partir de fontes abertas
e passou a usar um **corredor sintético** (gerado por
`src/pps57_sumo/build_network.py`, `make build`) como cenário único.

Ou seja, **toda esta pipeline de construção deixou de ter análogo** no caminho
principal: não há download/netconvert/snapping de paragens a partir de OSM/GTFS.
Manter estes scripts no caminho vivo seria enganador. Foram movidos para aqui (e
não apagados) para preservar o método e o histórico — continuam recuperáveis e
auditáveis.

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
| `run_tsp_demo.py` | Demo de valor baseline-vs-TSP no corredor OSM de Boavista. |

## Estado

- **Não mantidos** no caminho actual. Continuam a funcionar de forma autónoma
  (recalculam `ROOT` para a raiz do repo), mas dependem de descarregar dados de
  Porto/Madrid que podem ter mudado a montante.
- A capacidade **reutilizável** (matriz de conflitos autoritativa, perfil de rede
  empírico, parsers GTFS e de contagens) ficou no caminho vivo, em
  `src/pps57_sumo/{network_binding,network_profile}.py` e
  `src/pps57_sumo/validation/{gtfs_pt,reference_counts,metrics,acceptance}.py`.
- A cobertura de regressão destes scripts vive em
  `tests/test_legacy_porto_evidence.py`.

## Equivalente atual

Os instrumentos reutilizáveis correm no corredor sintético (`make build` gera a net):

```bash
make build
python scripts/run_network_binding_check.py        # matriz de conflitos na net do corredor
python scripts/empirical_network_profile_check.py --network sumo/network/corredor.net.xml
```
