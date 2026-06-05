#!/usr/bin/env python3
"""Export a deterministic SUMO network profile for map-agnostic TSP setup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.network_profile import load_network_profile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, default=ROOT / "sumo/network/corredor.net.xml")
    parser.add_argument("--additional", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "reports/network_profile.json")
    args = parser.parse_args()

    network = args.network if args.network.is_absolute() else ROOT / args.network
    additional_args = list(args.additional)
    default_detectors = ROOT / "sumo/additional/detectors.add.xml"
    if not additional_args and default_detectors.exists():
        additional_args.append(default_detectors)
    additional = [path if path.is_absolute() else ROOT / path for path in additional_args]
    profile = load_network_profile(network, additional_files=additional)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(profile.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "network_file": str(network),
                "output": str(args.output),
                "tls_count": len(profile.tls_profiles),
                "detector_count": len(profile.detectors),
                "fingerprint": profile.fingerprint,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
