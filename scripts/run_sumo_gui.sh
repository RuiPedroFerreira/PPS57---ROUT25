#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sumo-gui -c sumo/corredor.sumocfg
