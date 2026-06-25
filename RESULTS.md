# Results

_Documento gerado automaticamente por `scripts/run_sumo_scenario.py` a partir de
`reports/scenarios/scenario_suite_summary.json`. **Não editar à mão** — corre
`make scenario-suite` (opcionalmente após `make clean`) para o regenerar a partir
dos dados._

## Desenho da avaliação

Cada cenário é uma **comparação emparelhada** entre o braço `baseline` (o controlador
TSP em dry-run: decide mas nunca atua) e o braço `tsp_actuation` (idêntico, mas a
aplicar os comandos aprovados), sobre o conjunto de seeds configurado em
`scenario_profiles[*].random_seeds`. Como ambos os braços partilham procura, geometria
e seed, qualquer diferença medida é atribuível à atuação e não à máquina de execução.

Cada run cobre a janela completa de procura (`simulation_end_s`); a guarda
`--allow-short-horizon` impede que um `--steps` curto trunque a janela sem aviso.
Os ganhos por seed são emparelhados e reportados como média com intervalo de
confiança a 95 % (t-Student); o efeito só é **significativo quando o IC95 exclui
zero**. Com poucas seeds os intervalos são largos — uma direção consistente não é
o mesmo que um tamanho de efeito provado.

## Veredito de viabilidade vs. estimativa de efeito

Os gates de viabilidade (`max_collisions=0`, `max_teleports_jam=0`,
`max_waiting_to_insert`, …) são **fail-closed estritos**. Numa amostragem larga
(muitas seeds) dos cenários mais carregados, podem assinalar **eventos de cauda
raros por-seed** — uma colisão de seguimento denso ou um teleport de gridlock numa
seed isolada — que são reportados de forma **transparente** como `fail` de
viabilidade, em vez de mascarados. Esses são micro-eventos ao nível da simulação
numa seed específica e **não** invalidam o IC95 emparelhado do **efeito** do TSP
nesse cenário (a estimativa de efeito é a diferença baseline↔atuação na mesma
seed; um artefacto que ocorre numa seed afeta ambos os braços). Ler as duas
tabelas em conjunto: a de **impacto** mede o efeito; a de **viabilidade** sinaliza
seeds com micro-eventos a inspecionar, não uma falha do efeito medido.


**Cobertura:** 8 cenários · 318 runs SUMO · janela 7200s (~2.0h) · passo 0.5s.

## Impacto no transporte público (atraso dos autocarros)

Melhoria = redução do `buses.mean_time_loss_s` vs baseline (emparelhada por seed).

| Cenário | seeds (n) | Melhoria média (s) | IC95 (s) | Veredito estatístico | Δ ponto seed-base (%) |
|---|---:|---:|---:|---|---:|
| baseline_am_peak | 20 | +30.4 | [+18.4, +42.4] | significant_improvement | +13.3% |
| baseline_off_peak | 20 | +28.0 | [+20.9, +35.1] | significant_improvement | +16.9% |
| congested_am_peak | 20 | +29.6 | [+21.3, +38.0] | significant_improvement | +12.5% |
| cross_traffic_pressure | 20 | +26.8 | [+14.3, +39.3] | significant_improvement | +11.3% |
| delayed_bus_westbound | 19 | +33.3 | [+22.8, +43.8] | significant_improvement | +12.8% |
| bunched_buses | 20 | +16.7 | [+7.6, +25.9] | significant_improvement | +7.6% |
| emergency_vehicle_conflict | 20 | +30.1 | [+21.0, +39.1] | significant_improvement | +13.2% |
| congested_delayed_bus | 20 | +32.7 | [+23.3, +42.0] | significant_improvement | +12.7% |

## Impacto no tráfego geral

Mesma convenção: melhoria = redução do `general_traffic.mean_time_loss_s`. Um custo real do TSP aparece como `significant_regression`.

| Cenário | Melhoria média (s) | IC95 (s) | Veredito estatístico |
|---|---:|---:|---|
| baseline_am_peak | -7.9 | [-15.8, +0.0] | inconclusive_ci_includes_zero |
| baseline_off_peak | -5.7 | [-7.1, -4.2] | significant_regression |
| congested_am_peak | -8.8 | [-16.0, -1.5] | significant_regression |
| cross_traffic_pressure | -4.7 | [-14.0, +4.7] | inconclusive_ci_includes_zero |
| delayed_bus_westbound | -5.3 | [-16.9, +6.2] | inconclusive_ci_includes_zero |
| bunched_buses | +2.9 | [-3.2, +8.9] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | -5.5 | [-15.5, +4.4] | inconclusive_ci_includes_zero |
| congested_delayed_bus | -4.5 | [-14.9, +5.9] | inconclusive_ci_includes_zero |

## Métricas-foco por cenário (IC95 emparelhado)

Métricas específicas do cenário: veículo de emergência ou autocarro direcional.

| Cenário | Métrica | n | Melhoria média (s) | IC95 (s) | Veredito |
|---|---|---:|---:|---:|---|
| baseline_am_peak | Autocarro westbound · timeLoss | 20 | +2.8 | [-17.3, +22.9] | inconclusive_ci_includes_zero |
| baseline_am_peak | Autocarro eastbound · timeLoss | 20 | +59.4 | [+47.9, +70.9] | significant_improvement |
| baseline_off_peak | Autocarro westbound · timeLoss | 20 | +9.2 | [+1.6, +16.9] | significant_improvement |
| baseline_off_peak | Autocarro eastbound · timeLoss | 20 | +47.8 | [+40.4, +55.1] | significant_improvement |
| congested_am_peak | Autocarro westbound · timeLoss | 20 | +17.9 | [+1.3, +34.5] | significant_improvement |
| congested_am_peak | Autocarro eastbound · timeLoss | 20 | +42.0 | [+28.3, +55.7] | significant_improvement |
| cross_traffic_pressure | Autocarro westbound · timeLoss | 20 | +4.3 | [-15.4, +24.1] | inconclusive_ci_includes_zero |
| cross_traffic_pressure | Autocarro eastbound · timeLoss | 20 | +50.4 | [+33.9, +66.9] | significant_improvement |
| delayed_bus_westbound | Autocarro westbound · timeLoss | 19 | +29.3 | [+14.5, +44.1] | significant_improvement |
| delayed_bus_westbound | Autocarro eastbound · timeLoss | 19 | +37.5 | [+15.0, +60.0] | significant_improvement |
| bunched_buses | Autocarro westbound · timeLoss | 20 | +7.8 | [-8.0, +23.7] | inconclusive_ci_includes_zero |
| bunched_buses | Autocarro eastbound · timeLoss | 20 | +27.9 | [+14.9, +40.9] | significant_improvement |
| emergency_vehicle_conflict | Emergência · timeLoss | 20 | +22.9 | [-47.6, +93.4] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | Autocarro westbound · timeLoss | 20 | -2.4 | [-13.0, +8.2] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | Autocarro eastbound · timeLoss | 20 | +64.1 | [+51.4, +76.9] | significant_improvement |
| congested_delayed_bus | Autocarro westbound · timeLoss | 20 | +22.3 | [+3.6, +41.0] | significant_improvement |
| congested_delayed_bus | Autocarro eastbound · timeLoss | 20 | +43.5 | [+28.3, +58.7] | significant_improvement |

## Emissões (TSP vs baseline)

Total de frota por run; média entre seeds quando disponível. Negativo = redução.

| Cenário | CO2 Δ | Combustível Δ |
|---|---:|---:|
| baseline_am_peak | +1.6% | +1.6% |
| baseline_off_peak | +1.2% | +1.2% |
| congested_am_peak | +1.8% | +1.8% |
| cross_traffic_pressure | +0.9% | +0.9% |
| delayed_bus_westbound | +1.0% | +1.0% |
| bunched_buses | -0.5% | -0.5% |
| emergency_vehicle_conflict | +1.1% | +1.1% |
| congested_delayed_bus | +0.9% | +0.9% |

## Qualidade e viabilidade da simulação

| Cenário | Veredito | Horizonte (s) | seeds | Comparações |
|---|---|---:|---:|---:|
| baseline_am_peak | pass | 7200 | 20 | 1 |
| baseline_off_peak | pass | 7200 | 20 | 1 |
| congested_am_peak | pass | 7200 | 20 | 1 |
| cross_traffic_pressure | fail | 7200 | 20 | 1 |
| delayed_bus_westbound | pass | 7200 | 19 | 1 |
| bunched_buses | pass | 7200 | 20 | 1 |
| emergency_vehicle_conflict | pass | 7200 | 20 | 1 |
| congested_delayed_bus | fail | 7200 | 20 | 1 |

## Reprodução

```bash
make clean          # limpa outputs/reports de cenário (preserva os .md versionados)
make scenario-suite # corre os 8 cenários, ambos os braços, todas as seeds, janela completa
```

Os números acima são regenerados no fim de `make scenario-suite`. Para mais seeds (IC95 mais apertado) edita `scenario_profiles[*].random_seeds` em `configs/sumo_scenario_base.json` ou corre `make scenario-suite SUITE_SEEDS="17 42 57 …"`.
