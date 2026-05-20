# Cenário realista Porto/Boavista - v0.2

## Objetivo

Adaptar o cenário SUMO do SUMO Digital Twin para ficar mais próximo de um caso urbano real, sem bloquear o desenvolvimento por falta de dados operacionais completos.

O cenário v0.2 é um **proxy topológico realista**. Isto significa que a sua estrutura é inspirada num corredor real, mas ainda não substitui uma rede importada e calibrada com dados municipais.

## Corredor assumido

- Cidade: Porto
- Corredor: Avenida da Boavista
- Extremos funcionais: Casa da Música / Boavista até Praça do Império / Castelo do Queijo / ligação a Matosinhos
- Uso no PPS: validação de prioridade semafórica para transporte público rodoviário em ambiente C-ITS/V2X simulado

## Interseções representadas

| ID | Nome funcional | RSU proposta |
|---|---|---|
| I1 | Casa da Música / Boavista | RSU_BOAVISTA_01 |
| I2 | Av. Boavista / Bessa | RSU_BOAVISTA_02 |
| I3 | Av. Boavista / Antunes Guimarães | RSU_BOAVISTA_03 |
| I4 | Av. Boavista / Serralves | RSU_BOAVISTA_04 |
| I5 | Av. Boavista / Marechal Gomes da Costa | RSU_BOAVISTA_05 |
| I6 | Praça do Império | RSU_BOAVISTA_06 |
| I7 | Castelo do Queijo / ligação Matosinhos | RSU_BOAVISTA_07 |

## Linhas de transporte público simuladas

Foram criadas duas linhas proxy:

- STCP500_PROXY: representa uma linha centro/Boavista/Matosinhos.
- STCP502_PROXY: representa uma segunda linha com percurso sobreposto parcial e diferente frequência.

Estas linhas não devem ser interpretadas como cópia integral dos horários oficiais. Servem para testar pedidos concorrentes de prioridade, headways distintos e impacto sobre tráfego geral.

## Procura de tráfego

A procura é sintética, mas mais próxima de um cenário realista:

- duas horas de simulação;
- hora de ponta da manhã;
- maior fluxo no sentido oeste -> este, representando movimento inbound para a zona urbana central;
- fluxos secundários em todas as transversais;
- detetores E1/E2 para futura medição de volume, ocupação e filas.

## Semáforos

As interseções foram marcadas como `traffic_light` nos ficheiros Plain XML. O `netconvert` deve gerar a rede `.net.xml` com lógica semafórica inicial. Para execução local, o `Makefile` usa parâmetros de ciclo semafórico atuado como ponto de partida.

Para validação operacional, esta componente deve ser substituída por:

- grupos semafóricos reais;
- sequências de fases reais;
- amarelos e all-red reais;
- mínimos/máximos reais;
- offsets reais do corredor;
- restrições pedonais reais.

## Porque este cenário é mais realista que o v0.1

| Dimensão | v0.1 | v0.2 |
|---|---|---|
| Geografia | corredor genérico | corredor ancorado no Porto/Boavista |
| Interseções | I1-I5 genéricas | I1-I7 com nomes funcionais |
| Procura | simétrica e simples | assimétrica por hora de ponta |
| Transporte público | uma linha L500 fictícia | duas linhas proxy STCP500/STCP502 |
| Duração | 1h | 2h |
| Detetores | básicos | E1 e E2 em aproximações |
| Preparação C-ITS | indireta | RSU IDs e regras OBU/TSP já parametrizadas |

## Limitações

Este cenário ainda não valida desempenho real de tráfego. As conclusões quantitativas só devem ser usadas para comparar algoritmos em ambiente controlado, não para afirmar impacto operacional real na cidade.

## Caminho para transformar em gémeo digital real

1. Obter extrato OSM/JOSM do corredor.
2. Converter OSM para SUMO com `netconvert`.
3. Corrigir manualmente viragens, faixas, passadeiras e paragens.
4. Importar GTFS STCP para rotas e horários reais.
5. Introduzir planos semafóricos reais.
6. Calibrar volumes por movimento com contagens reais.
7. Validar tempos de viagem face a GPS/AVL ou floating-car.
8. Só depois usar KPIs como estimativas de impacto operacional.
