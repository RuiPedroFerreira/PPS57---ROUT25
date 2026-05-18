# Fontes de dados reais e como ligar ao cenário

## GTFS STCP

O Portal de Dados Abertos do Porto disponibiliza os horários, paragens e rotas da STCP em formato GTFS. O cenário v0.2 ainda não descarrega nem importa automaticamente esse ZIP; o objetivo é deixar o contrato técnico preparado.

Colocar o ZIP GTFS em:

```text
data/gtfs/gtfs_stcp_latest.zip
```

Depois, no hardening do Pacote 2, criar script para:

- ler `routes.txt`;
- selecionar linhas pretendidas, por exemplo 500/502/205 ou linhas definidas pelo projeto;
- ler `trips.txt`, `stop_times.txt` e `stops.txt`;
- gerar rotas/serviços SUMO ou alimentar `gtfs2pt.py` do SUMO.

## OSM / rede viária

Colocar extrato OSM em:

```text
data/osm/boavista_corridor.osm.xml
```

Depois converter com `netconvert`, corrigir geometrias com `netedit`/JOSM e substituir `sumo/plain/*` por uma rede real.

## Contagens e planos semafóricos

Colocar dados locais em:

```text
data/traffic_counts/
data/signal_plans/
```

A estrutura final deve permitir calibrar procura, fases e offsets por período horário.
