#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python src/pps57_sumo/build_network.py --config configs/sumo_scenario_base.json --base-dir sumo
