#!/usr/bin/env python3
"""Run the venv SUMO binary while discarding per-step emission output."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    real_sumo = root / ".venv" / "bin" / "sumo"
    if not real_sumo.exists():
        print(f"SUMO binary not found: {real_sumo}", file=sys.stderr)
        return 127

    filtered = []
    skip_next = False
    saw_emission_output = False
    for arg in sys.argv[1:]:
        if skip_next:
            filtered.append("/dev/null")
            skip_next = False
            continue
        if arg == "--emission-output":
            filtered.append(arg)
            skip_next = True
            saw_emission_output = True
            continue
        if arg.startswith("--emission-output="):
            filtered.append("--emission-output=/dev/null")
            saw_emission_output = True
            continue
        filtered.append(arg)
    if skip_next:
        # A trailing --emission-output with no value: drop the dangling flag
        # (there is nothing to skip), otherwise the rewritten command is broken.
        filtered.pop()
    if not saw_emission_output:
        filtered.extend(["--emission-output", "/dev/null"])

    os.execv(str(real_sumo), [str(real_sumo), *filtered])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
