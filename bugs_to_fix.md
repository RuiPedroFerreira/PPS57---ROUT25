# Bugs & Optimizações — scripts geradores de dados da plataforma

Inventário consolidado da revisão dos scripts que produzem os artefactos consumidos pelo dashboard Streamlit (`reports/**/*.json`, `outputs/**/*.jsonl`, etc.). Cada item identifica ficheiro, linhas, severidade e impacto. Os números (B1–B45, O1–O14) são estáveis para referência cruzada em PRs/issues.

Escopo coberto:

- `scripts/` — orquestradores SUMO, demonstrador TSP, comparações, avaliação de outcomes, dataset de eventos, scripts de diagnóstico/auditoria.
- `src/pps57_sumo/` — parsers de KPIs (tripinfo, emissions, insertion, detectors), build de rede, estatísticas, validação.
- `src/pps57_opt/` — demonstrator, dataset de eventos, outcome evaluator, AB compare.
- `src/pps57_dashboard/results.py` — descobridor de relatórios e scoreboard que alimenta o headline.

---

## Sumário

| Categoria | Críticos | Médios | Baixos | Optimizações |
|---|---:|---:|---:|---:|
| **Total** | **4** | **22** | **19** | **14** |

Temas transversais identificados (ver secção final): drift de contracto JSON entre scripts e dashboard; gates a passar silenciosamente quando faltam dados; mistura de níveis de agregação no mesmo relatório; `--steps` com semânticas inconsistentes entre scripts; reprodutibilidade incompleta nas réplicas multi-seed; fórmula errada de per-vehicle-km nas normalizações de emissões.

> **Revisão de verificação (2026-06-23).** Todos os itens foram confirmados contra o código commitado (`90d275b`). A esmagadora maioria é real. Correcções assinaladas inline abaixo:
> - **B31 — falso positivo** (premissa errada sobre a semântica do TraCI).
> - **B14**, **B45** e **O8 — parcialmente reais**: a mecânica existe, mas um detalhe da descrição está incorrecto.
>
> Os números mantêm-se estáveis (não foram renumerados); a contagem do Sumário acima reflecte os itens originalmente inventariados, não o veredicto pós-revisão.

> **Estado de resolução (2026-06-23).** Resolvidos no branch `fix/fail-closed-and-rl-tab`: **B1** (tab "vs RL" lê `decision_outcome_evaluation.json`), **B4** (gate fail-closed quando falta telemetria de `statistics.xml` — inclui o `int(float)` do parsing e o sinal `safety_statistics_complete`), **B26** (`safety_clean` só conta com telemetria presente), **B30** (auditoria de protocolo sai !=0, incl. `orphan_ssem`, e ligada ao `make`), **B33** (validação network-profile fail-closed com `--allow-unbuilt-network`).
>
> Optimizações resolvidas: **O3** (parse_tripinfo via `iterparse`), **O4** (cache de KPIs limitado com `lru_cache`), **O5** (lookup de SPATEM indexado, O(D·log S)), **O6** (streaming linha-a-linha dos `_read_jsonl`), **O9** (demonstrator reusa o runtime payload em vez de recomputar), **O10** (contagem de ficheiros sem materializar lista).
>
> Continuam **em aberto**: bugs B2, B3, B5–B25, B27–B29, B32, B34–B52; optimizações **O1/O2/O13** (reuso de netconvert entre seeds e single-pass tripinfo+emissions — exigem correr a suite SUMO para validar), **O7/O8** (ganho marginal vs risco em ficheiro crítico), **O11/O12** (impacto desprezável), **O14** (fiddly e arriscaria o teste do RESULTS.md).

---

## 🔴 Críticos (alta severidade — quebram directamente o que o dashboard mostra)

### B1 — Esquema JSON desalinhado: `compare-tsp-rl` vs dashboard

- **Ficheiros:** `scripts/compare_tsp_baseline_rl.py` (L139-144), `src/pps57_opt/ab_compare.py` (L68-91), `dashboard.py` (L1368, L2944-2989).
- **Bug:** `compare_tsp_baseline_rl.py` escreve `reports/tsp_baseline_vs_rl_comparison.json` com o payload `{comparison, baseline_mode, rl_mode, rows}` (runtime summary deltas). O tab "vs RL" do dashboard lê o mesmo ficheiro mas espera os campos do *outcome evaluator* (`matched_decision_count`, `network_impact_verdict`, `evaluation`, `verdict_counts`, `kpi_evaluation`), que estão em `reports/decision_outcome_evaluation.json` (produzido por `evaluate_decision_outcomes.py`).
- **Impacto:** depois de `make compare-tsp-rl`, o tab "vs RL" mostra `0` decisões, veredicto `"—"` e charts vazios mesmo com o ficheiro presente.
- **Fix sugerido:** o dashboard deve ler `decision_outcome_evaluation.json` para o tab RL — ou unificar os dois schemas com `evaluation`/`verdict_counts` também emitidos por `write_tsp_ab_comparison`.

### B2 — Suite summary sobrescrita por runs parciais

- **Ficheiros:** `scripts/run_sumo_scenario.py` (L157-166).
- **Bug:** qualquer invocação (mesmo `make scenario-run SCENARIO=...`) reescreve `reports/scenarios/scenario_suite_summary.json` apenas com os cenários daquela corrida.
- **Impacto:** o tab "Cenários" e o `RESULTS.md` perdem os restantes cenários; só `make scenario-suite` (corrida completa) deixa o ficheiro consistente.
- **Fix sugerido:** ler o ficheiro existente, fazer merge pelo `scenario_id` e só depois reescrever; ou apenas escrever a suite quando `args.all`.

### B3 — `--steps` com semântica inconsistente entre scripts

- **Ficheiros:** `scripts/run_sumo_scenario.py` (L64-72), `scripts/run_tsp_demonstrator.py` (L68-72, L217-218), `scripts/compare_tsp_baseline_rl.py` (L65), `scripts/evaluate_decision_outcomes.py` (L50-53), `scripts/sumo_smoke.py` (L36, L57-66).
- **Bug:**
  - `run_sumo_scenario`, `run_tsp_demonstrator` → `--steps` = contagem **TraCI steps** (×`step_length`).
  - `compare_tsp_baseline_rl`, `evaluate_decision_outcomes` → mesma semântica, mas com defaults `7200` que a `step_length=0.5s` truncam para 3600 s (metade da janela).
  - `sumo_smoke` → `--steps` é passado a SUMO como `--end` (**segundos**), default 1800.
- **Impacto:** comparações RL e outcome eval correm metade da janela configurada; smoke "1800 steps" corre realmente 1800 s (~3600 steps). Cross-report comparisons são triviais de mal-interpretar.
- **Fix sugerido:** uniformizar para TraCI steps em todos os scripts; manter um flag distinto (`--end-s`) onde seja seconds. Defaults devem alinhar-se com `simulation_end_s / step_length` ou ser `None` (corre janela inteira).

### B4 — Safety gates passam silenciosamente quando `statistics.xml` falha

- **Ficheiros:** `src/pps57_sumo/parse_insertion.py` (L60-62, L88-102); `scripts/run_sumo_scenario.py` `run_verdict` (L831-890); `collect_run_kpis` (L590-606).
- **Bug:** se `statistics.xml` falhar o parse (ou ausente), os contadores de safety (`collisions`, `teleports_*`, `emergency_braking`, `vehicles_waiting`) não existem no dict devolvido. `run_verdict` lê com `or 0`, logo todos os gates passam por defeito. Não há campo `statistics_available`/`statistics_parse_error` consumido pelo verdict.
- **Bug secundário:** `int("123.0")` em L88-102 lança `ValueError` que **não** é apanhado pelo `except (ET.ParseError, …)` — exceção não tratada em vez de flag de erro.
- **Impacto:** cenários sem dados de segurança aparecem como "pass" sem evidência.
- **Fix sugerido:** propagar `statistics_available` ao verdict (falha ou `inconclusive` quando `False`); usar `int(float(x))` como já faz o parsing do summary.

---

## 🟠 Médios (incorreções de agregação, semânticas inconsistentes, falhas silenciosas)

### Agregação multi-seed em `run_sumo_scenario.py`

- **B5 — Comparações usam o ficheiro KPI da primeira seed.**
  Ficheiro: `scripts/run_sumo_scenario.py` (L370-384, L750-805).
  `_aggregate_replications` faz `aggregate = dict(first)`, logo `runs[run_type]["kpis"]` aponta para o `kpis.json` da primeira seed apenas. `compare_scenario_runs` usa esse ficheiro para `bus_time_loss`, `general_traffic_time_loss`, `regression_pct` e gates absolutos (`>10%`, `>90s`, `queue>30`). Resultado: a comparação point-delta e os gates passam/falham com base numa seed, enquanto o IC95 emparelhado já é multi-seed. No `RESULTS.md` (L1165-1180) a coluna "Δ ponto seed-base (%)" é single-seed na mesma linha que o IC95 multi-seed.

- **B6 — `render_scenario_report` mostra KPIs da primeira seed sem sinalizar.**
  Ficheiro: `scripts/run_sumo_scenario.py` (L979-1000).

- **B7 — `n="1"` em `RESULTS.md` quando há várias seeds mas a significância não foi computada.**
  Ficheiro: `scripts/run_sumo_scenario.py` (L1172).

- **B8 — Seeds com `kpis.json` ilegível são silenciosamente descartadas.**
  Ficheiro: `scripts/run_sumo_scenario.py` `_compute_kpi_aggregate` (L380-381).
  Média/IC ficam sobre o subconjunto carregado, sem nota.

- **B9 — `stdev` populacional vs `ci95_*` amostral no mesmo agregado.**
  Ficheiro: `scripts/run_sumo_scenario.py` (L426-432). `stdev = pstdev(values)` mas `ci95_*` vem de `mean_ci95` (amostral).

- **B10 — p5/p95 aproximam min/max em n pequeno.**
  Ficheiro: `scripts/run_sumo_scenario.py` (L422-423).

### Parsers de XML

- **B11 — `vehicles` count vs `mean_*` usam denominadores diferentes.**
  Ficheiro: `src/pps57_sumo/parse_tripinfo.py` (L138-158).
  `vehicles = len(items)` (todas as linhas) mas as estatísticas filtram `None`. Sem sinal em `kpis.json`.

- **B12 — Falha de parse devolve dict mutilado.**
  Ficheiro: `src/pps57_sumo/parse_tripinfo.py` (L56-59, L97-107).
  Em erro devolve só `{"source", "error"}` sem blocos `all_vehicles`/`buses`. `run_verdict` interpreta como "sem veículos completados" → cenário falha pelo motivo errado.

- **B13 — Electricity reportado em Wh mas com sufixo `_mg`.**
  Ficheiro: `src/pps57_sumo/parse_emissions.py` (L48-49, L136-160).
  Todas as chaves de output (`totals_mg`, `bus_totals_mg`, `mean_per_vehicle_mg`) usam `_mg`, mas para electricity são watt-hours. Unidades enganadoras para qualquer consumer eléctrico.

- **B14 — Veículos sem atributos inflacionam `vehicle_count`/`bus_count`.** *(parcialmente real)*
  Ficheiro: `src/pps57_sumo/parse_emissions.py` (L62-115).
  `setdefault` cria buckets vazios; eles contam para o denominador mas não para `totals_mg`, biaseando per-vehicle normalisations.
  **⚠️ Correcção da revisão (2026-06-23):** a inflação de `vehicle_count`/`bus_count` é real no caminho `_ingest_step_vehicle` (`setdefault` em L69; contagens em L124/L161). Mas o enviesamento das *per-vehicle normalisations* **não se concretiza**: `mean_per_vehicle_mg` usa `samples` (que excluem `None`, L130-139) e o dashboard normaliza sobre `vehicles` do *tripinfo*, não sobre este `vehicle_count`. O caminho `_ingest_tripinfo` também não cria bucket vazio (`return` antes do `setdefault`).

- **B15 — Duplicação silenciosa em `id` repetido.**
  Ficheiro: `src/pps57_sumo/parse_emissions.py` (L36-56).
  Soma valores em `bucket.get(metric, 0) + float(value)` sem deduplicação/warn.

- **B16 — SUMO sentinela `-1` entra nas médias.**
  Ficheiro: `src/pps57_sumo/detector_kpis.py` (L31, L80-89).
  `_avg` filtra `None` mas não o sentinel `-1` ("sem veículos no intervalo") em speed/occupancy. Médias enviesadas para baixo em intervalos esparsos.

- **B17 — `max_queue` confunde "sem dados" com 0.**
  Ficheiro: `src/pps57_sumo/detector_kpis.py` (L62).
  `max(item["max_queue_vehicles"] or 0, …)` devolve 0 quando todos os edges não têm dados.

- **B18 — Falha no summary cancela o parse do statistics.**
  Ficheiro: `src/pps57_sumo/parse_insertion.py` (L60-62).
  Mesmo com `statistics.xml` válido, a função sai cedo perdendo todos os safety KPIs por uma falha noutro ficheiro.

### Demonstrador e outcome evaluator

- **B19 — Inconsistência `applied AND not no_actuation`.**
  Ficheiros: `src/pps57_opt/outcome_evaluator.py` (L164-165, L419-424); `src/pps57_opt/demonstrator.py` (L467-476).
  Outcome evaluator usa só `applied = bool(actuation.get("applied"))`; demonstrator network-delivery exige também `not no_actuation`. Veredictos divergem em `--no-actuation`.

- **B20 — `passes_primary_demonstrator_goal` aceita `traffic_loss_delta is None` como "ok".**
  Ficheiro: `src/pps57_opt/demonstrator.py` (L343-354).
  Corridas curtas/esparsas conseguem "pass" sem evidência de que o tráfego geral não pagou custo.

- **B21 — Política RL antiga é reusada sem aviso.**
  Ficheiro: `scripts/compare_tsp_baseline_rl.py` (L110-127).
  Re-treina só com `--train-rl` ou se o report não existe. Um `tabular_q_policy_report.json` antigo é usado silenciosamente sobre logs baseline novos.

- **B22 — `--baseline-root`/`--rl-root` só são honrados juntos.**
  Ficheiro: `scripts/evaluate_decision_outcomes.py` (L94-102).
  Passar só um faz fallback silencioso a um run SUMO novo, sobrescrevendo `outputs/`.

- **B23 — KPIs não auto-derivados dos roots.**
  Ficheiro: `scripts/evaluate_decision_outcomes.py` (L92-93, L147-155, L206-217).
  `network_impact_verdict` permanece `inconclusive_without_kpis` mesmo com `tripinfo.xml` debaixo do root.

- **B24 — Índices last-wins em duplicados.**
  Ficheiros: `src/pps57_opt/event_dataset.py` (L40-46 — SREM por correlation token); `src/pps57_opt/outcome_evaluator.py` (L389-390 — actuations por decision_id).
  Duplicados sobrescrevem entradas anteriores sem warning.

- **B25 — Chaves KPI compostas vs `KPI_META` simples.**
  Ficheiros: `src/pps57_opt/outcome_evaluator.py` (L269-276); `dashboard.py` (L2987-2989).
  Evaluator emite `"buses:mean_time_loss_s"`; dashboard procura no `KPI_META` por `metric_key` simples. Labels caem para chave crua mesmo que o tab passasse a ler o ficheiro certo.

### Descobridor do dashboard (`src/pps57_dashboard/results.py`)

- **B26 — `safety_clean` inflado por dados ausentes.**
  Ficheiro: `src/pps57_dashboard/results.py` (L466-469).
  `not mean(...) → True` para `None`, logo cenários sem rows de safety contam como "limpos" no headline.

- **B27 — Per-vehicle-km com fórmula errada.**
  Ficheiro: `src/pps57_dashboard/results.py` (L174-175, L360-374); produzido em `src/pps57_sumo/parse_tripinfo.py` (L143).
  Distância = `mean_route_length_m × vehicles` só é exacta se todos os veículos percorrerem o mesmo comprimento. Com rotas heterogéneas (típico em city-wide), todos os `total_*_mg_per_vehicle_km` (CO₂, NOx, fuel, ...) ficam enviesados.

- **B28 — Mixed dtypes em `"Valor"`.**
  Ficheiro: `src/pps57_dashboard/results.py` (L187-201).
  Loop genérico armazena `"Valor": value` cru; o ramo de emissões e o `load_scenario_run_table._add` coergem para float. Pode partir agregações pandas/plotly.

- **B29 — Dataset "disponível" sem `kpis.json`.**
  Ficheiro: `src/pps57_dashboard/results.py` (L41-42).
  `_has_scenario_reports` devolve `True` só pela presença de `scenario_suite_summary.json`. Combinado com B2, o dashboard pode pinar um dataset vazio.

### Outros scripts

- **B30 — Auditoria de protocolo nunca falha.**
  Ficheiro: `scripts/audit_protocol_lifecycle.py` (L29-32).
  Imprime `OK audit` e retorna 0 independentemente de `protocol_kpis.missing_final_ssem`, `invalid_state_transitions`, `actuation_errors`.

- **B31 — `traci.simulationStep(x)` interpretado como "step até x".** *(FALSO POSITIVO)*
  Ficheiro: `scripts/empirical_network_profile_check.py` (L115, L262-268).
  ~~`simulationStep(x)` avança **por** `x` segundos; o script soma `sim_time_s + duration` etc. como argumento, divergindo das labels `time_s` registadas. O `phase_trace_after_setPhaseDuration` fica não-fiável.~~
  **⚠️ Correcção da revisão (2026-06-23): falso positivo.** A premissa está invertida — no TraCI, `simulationStep(t)` avança **até** ao tempo absoluto `t`, não "por `t`". O código passa tempos-alvo absolutos (`sim_time_s + duration`, `+1.0`, `+4.0`) **e regista-os como label** (L262-268), logo coincidem em vez de divergirem; a trace é consistente. O único risco residual — arredondamento ao limite de passo quando o `step-length` não divide o alvo — é diferente do que o item descreve e tem impacto menor.

- **B32 — Contagens hard-coded no verifier de regressão.**
  Ficheiro: `scripts/verify_issue68_fix.py` (L114-116).
  `before=480`, `after=155` — qualquer drift na rede/config faz o teste falhar por motivos não relacionados com a regressão visada.

- **B33 — Validação fail-open quando rede está em falta.**
  Ficheiro: `src/pps57_sumo/validate_project.py` (L514-518).
  Descoberta de rede ausente imprime SKIP e devolve `ok` em vez de fail-closed. Perfis inválidos passam estática até `make build`.

---

## 🟡 Baixos (riscos menores, mas que se acumulam)

- **B34 — Linha de bus com 1 só partida emite headway `None` mas `departures=1`.**
  `src/pps57_sumo/parse_tripinfo.py` (L188-193). Dashboard descarta o row silenciosamente.

- **B35 — `mean_stop_count` deriva de `waitingCount`.**
  `src/pps57_sumo/parse_tripinfo.py` (L158). Mede episódios de paragem (incluindo congestão), não visitas a paragens, mas o dashboard rotula como "Paragens médias".

- **B36 — `mean_speed_mps = routeLength/duration` inclui paragens.**
  `src/pps57_sumo/parse_tripinfo.py` (L144-151). Diverge das speeds dos detectores.

- **B37 — Classificação de "bus" frágil.**
  `src/pps57_sumo/vehicle_classification.py` (L17-20). `startswith("bus")` apanha `business_car`; check de ID prefix é case-sensitive só para `"Bus"`, não `"BUS"`.

- **B38 — Aproximação 1.96 para `df>30`.**
  `src/pps57_sumo/stats.py` (L48-53). Viés ~4% no half-width em df=31. Estender tabela até df=120 elimina o viés.

- **B39 — `n=1` reporta `stdev_sample=0.0, ci95_*=0.0` em vez de `None`.**
  `src/pps57_sumo/stats.py` (L70-78). Consumer pode confundir com IC degenerado válido.

- **B40 — Incidentes estocásticos não re-sorteados por réplica.**
  `src/pps57_sumo/scenarios.py` (L542-600); update da seed em `scripts/run_sumo_scenario.py` (L464-465) só toca `random_seed`, não regenera incidentes. Contrato de replicação quebrado, latente porque nenhum perfil em produção usa `stochastic_incidents`.

- **B41 — Entradas não-dict em `stochastic_incidents` ignoradas silenciosamente.**
  `src/pps57_sumo/scenarios.py` (L559-561).

- **B42 — `int(float(simulation_end_s))` trunca horizontes fraccionários.**
  `src/pps57_sumo/build_network.py` (L193-194).

- **B43 — Parse error de `policy_optimization_summary.json` silenciado.**
  `scripts/run_tsp_demonstrator.py` (L159-165). Secção counterfactuals desaparece sem mensagem.

- **B44 — Tripinfo ausente prossegue snapshot com `kpis: null`.**
  `scripts/run_tsp_demonstrator.py` (L262-286). Sem warning ao operador.

- **B45 — Parsing de `network_state` quebra em `:` interno; `_float` não tolera não-numérico.** *(parcialmente real)*
  `src/pps57_opt/event_dataset.py` (L351-365, L392-394). Um JSONL corrupto aborta o build do dataset todo.
  **⚠️ Correcção da revisão (2026-06-23):** `_float` ser um `float(value)` cru (sem `try/except`) é real, mas a afirmação "um JSONL corrupto aborta o build todo" é **maioritariamente falsa**: o loop principal de linhas apanha `(ProtocolCodecError, TypeError, ValueError)` e salta a linha (L174-178), e `_network_metrics` também apanha `ValueError`. Existe **um** único call-site verdadeiramente desprotegido — a `_float` na chave de ordenação `raw_rows.sort(key=...)` (≈L143) — esse sim pode abortar o build. O `split(":", 1)` usa só o primeiro `:` como fronteira, pelo que um `:` interno no *valor* é preservado; só uma *chave* com `:` seria mal partida.

- **B46 — Smoke estrutural por substring.**
  `scripts/sumo_smoke.py` (L24-30, L81-85). Match case-insensitive na linha inteira; linhas benignas com "collision with" em contexto não relacionado podem falhar smoke.

- **B47 — `_compare_profile_to_traci` é one-directional.**
  `scripts/empirical_network_profile_check.py` (L147-187). TLS presentes no perfil offline mas ausentes do TraCI não são reportados.

- **B48 — `early_green_actuable` conflate.**
  `scripts/diagnose_tls_blockers.py` (L121, L133). Métrica conta TLS com **zero** problemas de **qualquer** tipo, mas o label fala de "early-green". Operacionalmente mistura green-extension blockers com elegibilidade de early-green.

- **B49 — `mv_by_edges` last-wins.**
  `scripts/diagnose_binding_coverage.py` (L56). Movimentos partilhando o mesmo par de edges atribuem-se ao último signal group.

- **B50 — Divisões sem zero guard.**
  `scripts/diagnose_binding_coverage.py` (L88-90). Rede sem movimentos → `ZeroDivisionError`.

- **B51 — `run_network_binding_check` veredito `noop` quando antes já era zero.**
  `scripts/run_network_binding_check.py` (L126-128). Pode mascarar verificação correcta como falha.

- **B52 — `_evidence_common.running_time_envelope` sem guards.**
  `scripts/_evidence_common.py` (L115-116). `KeyError`/`TypeError` cru em vez de validação clara.

---

## ⚙️ Optimizações relevantes

- **O1 — Netconvert corre por seed × run_type.**
  `scripts/run_sumo_scenario.py` (L470-476) chama `build_sumo_artifacts` em cada iteração de seed, embora a rede plana seja invariante ao seed (`src/pps57_sumo/build_network.py` L76-96; só routes/demand variam). Em suites multi-seed × 2 braços é o custo dominante. **Solução:** mover o build da rede para fora do loop de seeds, partilhando `corredor.net.xml`.

- **O2 — Dupla leitura de `tripinfo.xml`.**
  `scripts/run_sumo_scenario.py` `collect_run_kpis` (L590-606) chama `parse_tripinfo` e `parse_emissions` separadamente, cada um percorre o ficheiro inteiro.

- **O3 — `parse_tripinfo.py` ainda usa `ET.parse` (DOM completo).**
  `src/pps57_sumo/parse_tripinfo.py` (L56-57). Tripinfo city-wide é volumoso. `parse_emissions.py` já streama com `iterparse`.

- **O4 — `_load_kpis_cached` é unbounded + deepcopy.**
  `scripts/run_sumo_scenario.py` (L948-967). Cresce indefinidamente em processos longos; cada chamada faz `deepcopy` do dict carregado.

- **O5 — `event_dataset._latest_spatem` é O(decisions × SPATEMs).**
  `src/pps57_opt/event_dataset.py` (L47-49, L271-281). Domina o tempo de build em corridas longas. Indexar por timestamp num sorted array + bisect.

- **O6 — JSONL inteiros carregados em memória.**
  `src/pps57_opt/event_dataset.py` (L260-268), `src/pps57_opt/demonstrator.py` (L403-410), `src/pps57_opt/outcome_evaluator.py` (L456-461). Streaming linha-a-linha + agregação incremental suporta corridas multi-GB.

- **O7 — `_append` redefinido em cada iteração.**
  `src/pps57_dashboard/results.py` (L127-149). Closure recriada milhares de vezes ao percorrer uma suite.

- **O8 — Net XML parseado várias vezes nos scripts de diagnóstico.** *(parcialmente real)*
  `scripts/diagnose_binding_coverage.py` (L37-41), `scripts/diagnose_tls_blockers.py`, `scripts/analyze_issue68_all_red.py`. Memoizar `load_network_profile`.
  **⚠️ Correcção da revisão (2026-06-23):** o net XML é mesmo parseado várias vezes, **mas** a sub-afirmação "memoizar `load_network_profile`" parte de uma premissa errada: `load_network_profile` **já está memoizado** via `@lru_cache(maxsize=8)` (`src/pps57_sumo/network_profile.py:311`). A redundância vem de `_read_junction_tables` (`network_binding.py`, `ET.fromstring`) e de chamadas `ET.fromstring` directas (`diagnose_binding_coverage.py:41`), que é o que deve ser memoizado.

- **O9 — `demonstrator._runtime_delta` chama `_run_payload` duas vezes.**
  `src/pps57_opt/demonstrator.py` (L309-324). Recomputa counters/per-TLS stats/green-time/score attribution duas vezes para os mesmos runs.

- **O10 — `len(list(...rglob('*')))` só para imprimir contagem.**
  `scripts/compare_tsp_baseline_rl.py` (L151-153). Trocar por `sum(1 for _ in ...rglob('*'))`.

- **O11 — Legacy emission rows não limpam o `<timestep>` pai.**
  `src/pps57_sumo/parse_emissions.py` (L113-115). `elem.clear()` corre só no `<vehicle>`; em dumps legacy longos a subtree retém-se em memória.

- **O12 — `parse_insertion.py` statistics em `ET.parse` (DOM).**
  `src/pps57_sumo/parse_insertion.py` (L84). Summary já streama; statistics deveria fazê-lo também.

- **O13 — Sem hook para reaproveitar `corredor.net.xml` entre seeds.**
  `src/pps57_sumo/build_network.py` (L76-96). Relacionado com O1.

- **O14 — `render_results_doc` total_runs conta planeados.**
  `scripts/run_sumo_scenario.py` (L1155-1156). `len(seeds)*len(runs)` é o que estava configurado, não os que efectivamente terminaram. Coverage line do `RESULTS.md` pode sobrestimar após falhas parciais.

---

## Temas transversais

1. **Drift de contracto JSON.**
   Três schemas distintos disputam o mesmo nome no dashboard (B1, B25). Tornar os contratos explícitos com `dataclasses`/`TypedDict` + validação de leitura no dashboard evitaria que falhas se manifestem como "—" sem erro.

2. **Falhas silenciosas vs fail-closed.**
   Vários gates passam quando a evidência **não existe** (B4, B26, B30, B33). O padrão correcto já existe pontualmente (`run_verdict` distingue `inconclusive`) e deveria ser propagado sistemicamente: missing data ⇒ `inconclusive`, nunca `pass`.

3. **Mistura de níveis de agregação na mesma tabela.**
   `RESULTS.md` cruza colunas multi-seed (IC95 emparelhado) com colunas single-seed (Δ%, queue, emissões fallback) sem o sinalizar (B5-B7). Padronizar: usar `kpi_aggregate` sempre que disponível.

4. **Semântica de `--steps` inconsistente entre 5 scripts (B3).**
   Fonte recorrente de horizontes truncados. Um único contracto, com flag distinto (`--end-s`) onde fizer sentido.

5. **Reprodutibilidade de réplicas SUMO incompleta.**
   Build de rede e incidentes estocásticos não respeitam o ciclo de réplicas (O1, B40); paths SUMO globais (`outputs/tripinfo.xml` etc.) impedem paralelização (já documentado em `clear_global_sumo_outputs`).

6. **Per-vehicle-km incorrecto.**
   B27 + B11. A fórmula `mean_route_length × vehicles` propaga-se pelos dois loaders do dashboard. Substituir por Σ route_length por classe daria valores correctos quando as rotas são heterogéneas (típico em city-wide).

---

## Plano de ataque sugerido (PRs incrementais)

| # | Tema | Tickets | Risco | Notas |
|---|---|---|---|---|
| 1 | Headline RL volta a aparecer | B1 | Baixo | Patch dashboard ou `compare_tsp_baseline_rl` para emitir o esquema de outcome. |
| 2 | Preservar suite summary | B2 | Baixo | Merge incremental por `scenario_id`. |
| 3 | Fail-closed em safety/discoberta | B4, B26, B30, B33 | Médio | Propagar flags `*_available` aos verdicts; `audit_protocol_lifecycle` deve sair !=0. |
| 4 | Agregação multi-seed coerente | B5-B10 | Médio | `compare_scenario_runs` deve usar `kpi_aggregate` quando há ≥2 seeds. |
| 5 | Uniformizar `--steps` | B3 | Baixo | Renomear smoke para `--end-s`; alinhar defaults com janela configurada. |
| 6 | Parsers (denominators, sentinelas, unidades) | B11-B18 | Médio | Refactor focado; preservar testes. |
| 7 | Per-vehicle-km correcto | B11, B27 | Médio | Reportar Σ route_length por classe em `parse_tripinfo`. |
| 8 | Optimizações I/O e netconvert | O1-O3, O11-O13 | Baixo | Streaming + reuso de `corredor.net.xml` entre seeds. |
| 9 | Demonstrator/outcome semantics | B19-B25 | Médio | Alinhar definição de "applied" e schema de chaves KPI. |
| 10 | Diagnósticos | B30-B33, B46-B52 | Baixo | Maioritariamente guards e validações de saída. |

Cada PR deve incluir um teste de regressão dirigido (em `tests/`) que falhe antes do fix e passe depois.
