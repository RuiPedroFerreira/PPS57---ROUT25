# Calibração e passagem do cenário realista para dados reais

## Situação atual v0.2

O cenário v0.2 já está ancorado num corredor real do Porto, mas usa aproximações para:

- geometria das interseções;
- número de faixas por segmento;
- volumes de tráfego;
- horários e frequências dos autocarros;
- planos semafóricos;
- tempos de paragem;
- comportamento de peões e atravessamentos.

## Significado prático de substituir por dados reais

Substituir por dados reais significa trocar as aproximações do ficheiro `configs/corridor_config.json` e dos XML SUMO por informação observada ou oficial.

Exemplos:

| Componente atual | Substituir por |
|---|---|
| Nós I1-I7 aproximados | geometria OSM/GIS real das interseções |
| Linhas STCP500_PROXY/STCP502_PROXY | GTFS STCP com rotas, trips, stop_times e stops reais |
| Frequência fixa 10/15 min | headways e horários reais por período |
| Fluxos sintéticos | contagens reais por acesso e por movimento |
| Semáforos gerados pelo netconvert | planos reais dos controladores |
| Dwell time fixo 20s | distribuição real de tempo em paragem |

## Fontes de dados candidatas

- Portal de Dados Abertos do Porto: dataset GTFS STCP.
- STCP: catálogo e horários de linhas.
- OpenStreetMap/JOSM: geometria inicial da rede.
- Município/operador semafórico: planos de sinais e grupos semafóricos.
- Operador de transporte: AVL/GPS e pontualidade.
- Contagens manuais ou sensores: volumes, viragens e filas.

## Sequência recomendada

1. Importar rede OSM para SUMO.
2. Corrigir rede com JOSM/netedit.
3. Importar GTFS para linhas reais.
4. Inserir paragens com coordenadas reais.
5. Inserir planos semafóricos.
6. Ajustar procura de tráfego.
7. Executar baseline.
8. Comparar tempos de viagem simulados vs observados.
9. Iterar até erro aceitável.

## Nota para avaliação PPS

A v0.2 é adequada para demonstrar arquitetura, módulos e integração SUMO-CITS-TSP. A calibração real é necessária para claims quantitativos, por exemplo redução percentual de atrasos em operação real.
