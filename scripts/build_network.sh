#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python src/pps57_sumo/generate_plain_corridor.py --config configs/corridor_config.json --output sumo/plain
mkdir -p sumo/network
netconvert   --node-files sumo/plain/corredor.nod.xml   --edge-files sumo/plain/corredor.edg.xml   --output-file sumo/network/corredor.net.xml   --no-turnarounds true   --tls.default-type actuated   --tls.cycle.time 90   --tls.yellow.time 3
