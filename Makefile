.PHONY: validate build run gui kpis clean

validate:
	python src/pps57_sumo/validate_project.py --root .

build:
	python src/pps57_sumo/generate_plain_corridor.py --config configs/corridor_config.json --output sumo/plain
	netconvert --node-files sumo/plain/corredor.nod.xml --edge-files sumo/plain/corredor.edg.xml --output-file sumo/network/corredor.net.xml --no-turnarounds true --tls.default-type actuated --tls.cycle.time 90 --tls.yellow.time 3

run: build
	sumo -c sumo/corredor.sumocfg --duration-log.statistics

kpis:
	python src/pps57_sumo/parse_tripinfo.py --tripinfo outputs/tripinfo.xml --out reports/baseline_kpis.json

gui: build
	sumo-gui -c sumo/corredor.sumocfg

clean:
	rm -f outputs/*.xml reports/*.json sumo/network/*.net.xml
