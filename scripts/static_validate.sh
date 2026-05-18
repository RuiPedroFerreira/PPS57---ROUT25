#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python src/pps57_sumo/validate_project.py --root .
