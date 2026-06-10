#!/usr/bin/env python3
"""Run the sim-to-real validation harness over user-supplied real-data inputs.

This is the V0 measuring instrument. It does NOT generate or assume any data:
you pass in files of ``(modelled, observed)`` pairs gathered from real sources
(CMP/IMT counts, STCP AVL/GTFS travel times, reference scenarios) and it emits a
source-traced acceptance report. With no inputs it exits with an error rather
than inventing anything.

Input formats (JSON lists):

  --link-flows      [{"link_id": "I2_I3", "modelled_veh_h": 870,
                      "observed_veh_h": 812, "source": "CMP detector 2026-..."}]
  --travel-times    [{"segment_id": "casa_musica->serralves",
                      "modelled_s": 540, "observed_s": 505, "source": "STCP AVL ..."}]
  --tsp-face-validity
                    [{"metric": "bus_running_time_improvement_pct",
                      "value_pct": 9.4, "source": "scenario delayed_bus_westbound"}]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation import (  # noqa: E402
    acceptance,
    load_validation_config,
)


def _load_json_list(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}, got {type(data).__name__}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None, help="validation_config.json (defaults to configs/)")
    parser.add_argument("--link-flows", type=Path, help="JSON list of modelled/observed link flows")
    parser.add_argument("--travel-times", type=Path, help="JSON list of modelled/observed journey times")
    parser.add_argument("--tsp-face-validity", type=Path, help="JSON list of measured TSP gains")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "validation" / "model_validation.json")
    args = parser.parse_args()

    if not any([args.link_flows, args.travel_times, args.tsp_face_validity]):
        parser.error(
            "no inputs given. This harness validates real (modelled, observed) data and "
            "invents nothing: pass at least one of --link-flows / --travel-times / --tsp-face-validity."
        )

    config = load_validation_config(args.config)
    report: Dict[str, Any] = {"config_status_note": config.get("status_note", "")}
    verdicts: List[str] = []

    if args.link_flows:
        result = acceptance.evaluate_link_flow_calibration(_load_json_list(args.link_flows), config)
        report["link_flow_calibration"] = result
        verdicts.append(result["verdict"])
    if args.travel_times:
        result = acceptance.evaluate_travel_times(_load_json_list(args.travel_times), config)
        report["travel_time_validation"] = result
        verdicts.append(result["verdict"])
    if args.tsp_face_validity:
        result = acceptance.evaluate_tsp_face_validity(_load_json_list(args.tsp_face_validity), config)
        report["tsp_face_validity"] = result
        verdicts.append(result["verdict"])

    # "no_measurements" (e.g. an empty --tsp-face-validity list) must NOT pass: the
    # harness refuses to report success on empty/missing data.
    if "fail" in verdicts or "no_measurements" in verdicts:
        report["overall_verdict"] = "fail"
    elif "flagged" in verdicts:
        report["overall_verdict"] = "flagged"
    else:
        report["overall_verdict"] = "pass"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    # Exit non-zero unless every gate passes: a "flagged" face-validity result must not
    # report success to an automated validation step.
    if report["overall_verdict"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
