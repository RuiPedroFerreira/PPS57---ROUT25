.PHONY: validate build run gui kpis cits-dryrun cits-sumo tsp-dryrun tsp-sumo tsp-sumo-no-actuation tsp-gui tsp-gui-no-actuation optimize-offline train-rl-policy platform platform-api platform-check platform-demo-data dashboard sort-routes test clean

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

validate:
	$(PYTHON) src/pps57_sumo/validate_project.py --root .
	$(PYTHON) -m json.tool configs/cits_config.json >/dev/null
	$(PYTHON) -m json.tool configs/tsp_config.json >/dev/null
	$(PYTHON) -m json.tool configs/policy_optimization_config.json >/dev/null
	$(PYTHON) -m json.tool configs/platform_config.json >/dev/null

build:
	$(PYTHON) src/pps57_sumo/generate_plain_corridor.py --config configs/corridor_config.json --output sumo/plain
	netconvert --node-files sumo/plain/corredor.nod.xml --edge-files sumo/plain/corredor.edg.xml --output-file sumo/network/corredor.net.xml --no-turnarounds true --tls.default-type static --tls.cycle.time 90 --tls.yellow.time 3

run: build
	sumo -c sumo/corredor.sumocfg --duration-log.statistics

kpis:
	$(PYTHON) src/pps57_sumo/parse_tripinfo.py --tripinfo outputs/tripinfo.xml --out reports/baseline_kpis.json

gui: build
	sumo-gui -c sumo/corredor.sumocfg

cits-dryrun:
	$(PYTHON) scripts/run_cits_emulation.py --mode dry-run --steps 60

cits-sumo: build
	$(PYTHON) scripts/run_cits_emulation.py --mode sumo --steps 7200

tsp-dryrun:
	$(PYTHON) scripts/run_tsp_control.py --mode dry-run --steps 90

tsp-sumo: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --steps 7200

tsp-sumo-no-actuation: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --steps 7200 --no-actuation

tsp-gui: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --gui --steps 7200

tsp-gui-no-actuation: build
	$(PYTHON) scripts/run_tsp_control.py --mode sumo --gui --steps 7200 --no-actuation

optimize-offline:
	$(PYTHON) scripts/run_policy_optimization.py

train-rl-policy:
	$(PYTHON) scripts/run_rl_training.py

platform-check:
	$(PYTHON) scripts/check_platform_data.py

platform-demo-data:
	$(PYTHON) scripts/generate_platform_demo_data.py --overwrite

platform:
	$(PYTHON) -m streamlit run dashboard/app.py

platform-api:
	$(PYTHON) scripts/run_platform_api.py

dashboard: platform

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
