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


**Cobertura:** 8 cenários · 80 runs SUMO · janela 7200s (~2.0h) · passo 0.5s.

## Impacto no transporte público (atraso dos autocarros)

Melhoria = redução do `buses.mean_time_loss_s` vs baseline (emparelhada por seed).

| Cenário | seeds (n) | Melhoria média (s) | IC95 (s) | Veredito estatístico | Δ ponto seed-base (%) |
|---|---:|---:|---:|---|---:|
| baseline_am_peak | 5 | +18.5 | [-23.2, +60.2] | inconclusive_ci_includes_zero | +8.0% |
| baseline_off_peak | 5 | +25.8 | [+6.6, +45.0] | significant_improvement | +15.6% |
| congested_am_peak | 5 | +37.9 | [+20.6, +55.3] | significant_improvement | +15.7% |
| cross_traffic_pressure | 5 | +15.1 | [-26.2, +56.5] | inconclusive_ci_includes_zero | +6.2% |
| delayed_bus_westbound | 5 | +36.7 | [+9.1, +64.2] | significant_improvement | +13.8% |
| bunched_buses | 5 | +17.4 | [-15.8, +50.5] | inconclusive_ci_includes_zero | +7.7% |
| emergency_vehicle_conflict | 5 | +31.2 | [+13.2, +49.1] | significant_improvement | +13.4% |
| congested_delayed_bus | 5 | +23.6 | [+1.3, +45.9] | significant_improvement | +9.7% |

## Impacto no tráfego geral

Mesma convenção: melhoria = redução do `general_traffic.mean_time_loss_s`. Um custo real do TSP aparece como `significant_regression`.

| Cenário | Melhoria média (s) | IC95 (s) | Veredito estatístico |
|---|---:|---:|---|
| baseline_am_peak | +5.2 | [-9.3, +19.7] | inconclusive_ci_includes_zero |
| baseline_off_peak | -4.0 | [-8.6, +0.6] | inconclusive_ci_includes_zero |
| congested_am_peak | -3.0 | [-25.7, +19.8] | inconclusive_ci_includes_zero |
| cross_traffic_pressure | +1.7 | [-35.5, +38.9] | inconclusive_ci_includes_zero |
| delayed_bus_westbound | -5.4 | [-16.6, +5.7] | inconclusive_ci_includes_zero |
| bunched_buses | +1.6 | [-10.3, +13.5] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | +2.3 | [-13.0, +17.6] | inconclusive_ci_includes_zero |
| congested_delayed_bus | -13.9 | [-43.5, +15.6] | inconclusive_ci_includes_zero |

## Métricas-foco por cenário (IC95 emparelhado)

Métricas específicas do cenário: veículo de emergência ou autocarro direcional.

| Cenário | Métrica | n | Melhoria média (s) | IC95 (s) | Veredito |
|---|---|---:|---:|---:|---|
| baseline_am_peak | Autocarro westbound · timeLoss | 5 | -13.7 | [-77.9, +50.4] | inconclusive_ci_includes_zero |
| baseline_am_peak | Autocarro eastbound · timeLoss | 5 | +52.4 | [+24.2, +80.6] | significant_improvement |
| baseline_off_peak | Autocarro westbound · timeLoss | 5 | +5.8 | [-16.0, +27.6] | inconclusive_ci_includes_zero |
| baseline_off_peak | Autocarro eastbound · timeLoss | 5 | +46.9 | [+27.4, +66.5] | significant_improvement |
| congested_am_peak | Autocarro westbound · timeLoss | 5 | +20.5 | [-8.7, +49.6] | inconclusive_ci_includes_zero |
| congested_am_peak | Autocarro eastbound · timeLoss | 5 | +56.2 | [+40.1, +72.4] | significant_improvement |
| cross_traffic_pressure | Autocarro westbound · timeLoss | 5 | -5.1 | [-65.9, +55.8] | inconclusive_ci_includes_zero |
| cross_traffic_pressure | Autocarro eastbound · timeLoss | 5 | +36.3 | [-42.9, +115.5] | inconclusive_ci_includes_zero |
| delayed_bus_westbound | Autocarro westbound · timeLoss | 5 | +31.0 | [-6.5, +68.6] | inconclusive_ci_includes_zero |
| delayed_bus_westbound | Autocarro eastbound · timeLoss | 5 | +42.6 | [-34.9, +120.1] | inconclusive_ci_includes_zero |
| bunched_buses | Autocarro westbound · timeLoss | 5 | +5.8 | [-61.7, +73.3] | inconclusive_ci_includes_zero |
| bunched_buses | Autocarro eastbound · timeLoss | 5 | +31.8 | [-6.9, +70.5] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | Emergência · timeLoss | 5 | -69.4 | [-366.6, +227.8] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | Autocarro westbound · timeLoss | 5 | -8.4 | [-37.5, +20.7] | inconclusive_ci_includes_zero |
| emergency_vehicle_conflict | Autocarro eastbound · timeLoss | 5 | +72.7 | [+41.5, +103.9] | significant_improvement |
| congested_delayed_bus | Autocarro westbound · timeLoss | 5 | +11.1 | [-12.1, +34.4] | inconclusive_ci_includes_zero |
| congested_delayed_bus | Autocarro eastbound · timeLoss | 5 | +36.6 | [+13.1, +60.2] | significant_improvement |

## Emissões (TSP vs baseline)

Total de frota por run; média entre seeds quando disponível. Negativo = redução.

| Cenário | CO2 Δ | Combustível Δ |
|---|---:|---:|
| baseline_am_peak | -1.2% | -1.2% |
| baseline_off_peak | +0.9% | +0.9% |
| congested_am_peak | +0.7% | +0.7% |
| cross_traffic_pressure | -0.9% | -0.9% |
| delayed_bus_westbound | +0.9% | +0.9% |
| bunched_buses | -0.3% | -0.3% |
| emergency_vehicle_conflict | -0.5% | -0.5% |
| congested_delayed_bus | +2.6% | +2.6% |

## Qualidade e viabilidade da simulação

| Cenário | Veredito | Horizonte (s) | seeds | Comparações |
|---|---|---:|---:|---:|
| baseline_am_peak | pass | 7200 | 5 | 1 |
| baseline_off_peak | pass | 7200 | 5 | 1 |
| congested_am_peak | pass | 7200 | 5 | 1 |
| cross_traffic_pressure | pass | 7200 | 5 | 1 |
| delayed_bus_westbound | pass | 7200 | 5 | 1 |
| bunched_buses | pass | 7200 | 5 | 1 |
| emergency_vehicle_conflict | pass | 7200 | 5 | 1 |
| congested_delayed_bus | pass | 7200 | 5 | 1 |

## Reprodução

```bash
make clean          # limpa outputs/reports de cenário (preserva os .md versionados)
make scenario-suite # corre os 8 cenários, ambos os braços, todas as seeds, janela completa
```

Os números acima são regenerados no fim de `make scenario-suite`. Para mais seeds (IC95 mais apertado) edita `scenario_profiles[*].random_seeds` em `configs/sumo_scenario_base.json` ou corre `make scenario-suite SUITE_SEEDS="17 42 57 …"`.
