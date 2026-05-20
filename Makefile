.PHONY: validate build run gui kpis cits-dryrun cits-sumo tsp-dryrun tsp-sumo tsp-sumo-no-actuation tsp-gui tsp-gui-no-actuation optimize-offline platform platform-check platform-demo-data dashboard test clean

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

validate:
	$(PYTHON) src/pps57_sumo/validate_project.py --root .
	$(PYTHON) -m json.tool configs/cits_config.json >/dev/null
	$(PYTHON) -m json.tool configs/tsp_config.json >/dev/null
	$(PYTHON) -m json.tool configs/optimization_config.json >/dev/null
	$(PYTHON) -m json.tool configs/platform_config.json >/dev/null

build:
	$(PYTHON) src/pps57_sumo/generate_plain_corridor.py --config configs/corridor_config.json --output sumo/plain
	netconvert --node-files sumo/plain/corredor.nod.xml --edge-files sumo/plain/corredor.edg.xml --output-file sumo/network/corredor.net.xml --no-turnarounds true --tls.default-type actuated --tls.cycle.time 90 --tls.yellow.time 3

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
	$(PYTHON) scripts/run_pacote5_optimization.py

platform-check:
	$(PYTHON) scripts/check_platform_data.py

platform-demo-data:
	$(PYTHON) scripts/generate_platform_demo_data.py --overwrite

platform:
	$(PYTHON) -m streamlit run dashboard/app.py

dashboard: platform

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

clean:
	rm -f outputs/*.xml outputs/*.csv outputs/*.json outputs/*.jsonl reports/*.json sumo/network/*.net.xml
