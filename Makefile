.PHONY: validate build run gui kpis cits-sumo compare-tsp-rl compare-sumo-kpis evaluate-decision-outcomes build-event-training-dataset tsp-sumo tsp-sumo-no-actuation tsp-gui tsp-gui-no-actuation optimize-offline train-rl-policy platform-api platform-check sort-routes test clean

# Hardening: cada receita corre como `bash -ec`, garantindo `set -e` mesmo
# em linhas encadeadas e abortando à primeira falha. Sem isto, alguém a
# adicionar `cmd1; cmd2` no futuro pode engolir erros do `cmd1`.
SHELL := /bin/bash
.SHELLFLAGS := -ec

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

validate:
	$(PYTHON) src/pps57_sumo/validate_project.py --root .
	$(PYTHON) -m json.tool configs/cits_config.json >/dev/null
	$(PYTHON) -m json.tool configs/tsp_config.json >/dev/null
	$(PYTHON) -m json.tool configs/policy_optimization_config.json >/dev/null
	$(PYTHON) -m json.tool configs/platform_config.json >/dev/null

# `validate` corre antes de `build` para que toda a cadeia (run/cits-sumo/
# tsp-sumo/etc., que dependem de build) execute o gate fail-closed de XML
# bem-formado e rotas ordenadas. Antes ficava órfão e podia ser ignorado.
build: validate
	$(PYTHON) src/pps57_sumo/generate_plain_corridor.py --config configs/corridor_config.json --output sumo/plain
	netconvert --node-files sumo/plain/corredor.nod.xml --edge-files sumo/plain/corredor.edg.xml --output-file sumo/network/corredor.net.xml --no-turnarounds true --tls.default-type static --tls.cycle.time 90 --tls.yellow.time 3

run: build
	sumo -c sumo/corredor.sumocfg --duration-log.statistics

kpis:
	$(PYTHON) src/pps57_sumo/parse_tripinfo.py --tripinfo outputs/tripinfo.xml --out reports/baseline_kpis.json

gui: build
	sumo-gui -c sumo/corredor.sumocfg

cits-sumo: build
	$(PYTHON) scripts/run_cits_emulation.py --mode sumo --steps 7200

compare-tsp-rl: build
	$(PYTHON) scripts/compare_tsp_baseline_rl.py --steps 7200 --train-rl

# Compara dois ficheiros de KPIs SUMO já gerados (parse_tripinfo). Os caminhos
# são overridable: make compare-sumo-kpis BASELINE_KPIS=... RL_KPIS=...
BASELINE_KPIS ?= reports/baseline_kpis.json
RL_KPIS ?= reports/rl_kpis.json
compare-sumo-kpis:
	$(PYTHON) scripts/compare_sumo_kpis.py --baseline-kpis $(BASELINE_KPIS) --rl-kpis $(RL_KPIS)

evaluate-decision-outcomes: build
	$(PYTHON) scripts/evaluate_decision_outcomes.py --steps 7200 --train-rl

build-event-training-dataset:
	$(PYTHON) scripts/build_event_training_dataset.py

tsp-sumo: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --steps 7200

tsp-sumo-no-actuation: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --steps 7200 --no-actuation

tsp-gui: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --gui --steps 7200

tsp-gui-no-actuation: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --gui --steps 7200 --no-actuation

optimize-offline: build-event-training-dataset
	$(PYTHON) scripts/run_policy_optimization.py

train-rl-policy: build-event-training-dataset
	$(PYTHON) scripts/run_rl_training.py

platform-check:
	$(PYTHON) scripts/check_platform_data.py

platform-api:
	$(PYTHON) scripts/run_platform_api.py

sort-routes:
	# Item 15: wrapper sobre $SUMO_HOME/tools/route/sort_routes.py.
	# Útil após edição manual de sumo/routes/routes.rou.xml; o `make validate`
	# falha se as rotas ficarem fora de ordem temporal.
	$(PYTHON) -m pps57_sumo.sort_routes

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

clean:
	# L3: limpar APENAS artefactos conhecidos gerados pelos pipelines; o glob
	# anterior (reports/*.json, outputs/*.json) varria snapshots do utilizador
	# (platform_snapshot.json, baseline_kpis.json) sem distinguir.
	rm -f outputs/tripinfo.xml outputs/summary.xml outputs/statistics.xml
	rm -f outputs/cits_messages.jsonl outputs/cits_mapem_snapshot.json outputs/cits_spatem_snapshot.json
	rm -f outputs/tsp_decisions.jsonl outputs/tsp_actuation.jsonl
	rm -f outputs/offline_policy_samples.jsonl outputs/policy_candidates.jsonl
	rm -f reports/cits_emulation_summary.json reports/tsp_emulation_summary.json
	rm -f reports/policy_report.json reports/policy_optimization_summary.json
	rm -f reports/tabular_q_policy_report.json reports/rl_training_summary.json
	rm -f reports/baseline_kpis.json
	rm -f sumo/network/corredor.net.xml
