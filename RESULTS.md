# Results

_Documento gerado automaticamente por `scripts/run_sumo_scenario.py` a partir de
`reports/scenarios/scenario_suite_summary.json`. **Não editar à mão** — corre
`make scenario-suite` (opcionalmente após `make clean`) para o regenerar a partir
dos dados._

## Estado atual: resultados por gerar

As tabelas quantitativas desta secção são preenchidas pela suite de cenários. O
conteúdo numérico anterior foi **removido** porque não era rastreável aos dados
commitados:

- descrevia um estudo de **3 seeds (17, 42, 57) / 48 runs de 2 h** com IC95 e teste-t
  emparelhado, mas em disco só existia **a seed 57** e `comparisons: {}` (nenhuma
  comparação calculada);
- **7 dos 8 cenários tinham corrido apenas 1 h** (7200 passos × 0,5 s = 3600 s) em vez
  das 2 h configuradas, por se ter passado `--steps 7200` (passos, não segundos) — só o
  `emergency_vehicle_conflict` correu as 2 h completas (14400 passos);
- os deltas tabelados não coincidiam com os da seed 57 em disco.

As correções que tornam a próxima execução consistente já estão aplicadas:

1. `make scenario-suite` corre agora **ambos os braços** (baseline + tsp_actuation) e
   **todas as seeds** do config, gerando as comparações emparelhadas e os IC95;
2. `scripts/run_sumo_scenario.py` **recusa** um `--steps` que trunque a janela de
   procura (a não ser com `--allow-short-horizon`), pelo que nenhum cenário volta a
   correr meia janela em silêncio;
3. este `RESULTS.md` passa a ser **gerado a partir do summary**, garantindo que os
   números são sempre os dos runs reais.

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

## Reprodução

```bash
make clean          # limpa outputs/reports de cenário (preserva os .md versionados)
make scenario-suite # corre os 8 cenários, ambos os braços, todas as seeds, janela completa
```

Ao terminar, `make scenario-suite` reescreve este ficheiro com as tabelas de impacto
no transporte público, tráfego geral, emissões e qualidade da simulação, todas
derivadas de `reports/scenarios/scenario_suite_summary.json`. Para apertar os IC95,
acrescenta seeds em `scenario_profiles[*].random_seeds` ou corre
`make scenario-suite SUITE_SEEDS="17 42 57 …"`.
