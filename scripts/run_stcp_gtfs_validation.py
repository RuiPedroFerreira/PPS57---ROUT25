#!/usr/bin/env python3
"""V3: validate the scenario's proxy public transport against the real STCP GTFS.

Extracts real weekday headways for the corridor lines from the pinned STCP GTFS
feed (the source of truth, fetched by scripts/fetch_stcp_gtfs.py) and compares
them to the proxy `public_transport.services` headways in the scenario config.
It does not rewrite the config (the real-stop -> synthetic-edge mapping needs real
geometry, which is V4); it produces a tracked, provenance-stamped validation
report. Nothing is invented — every real number comes from the GTFS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from statistics import median
import sys
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation.gtfs_pt import extract_corridor_headways  # noqa: E402

CORRIDOR_LINES = ["500", "502", "204"]
MODELLED_LINES = ["500", "502"]  # the lines the scenario config actually models
SANITY_BAND = (0.5, 2.0)  # order-of-magnitude sanity check (NOT a calibration standard)


def _proxy_headways_min_by_line(config: Dict) -> Dict[str, List[float]]:
    """Collect proxy headways (minutes) per corridor line from public_transport.services."""
    out: Dict[str, List[float]] = {}
    for service in config.get("public_transport", {}).get("services", []):
        digits = re.findall(r"\d+", str(service.get("line_id", "")))
        if not digits:
            continue
        short = digits[0]
        values = [float(service.get("headway_s", 0)) / 60.0]
        for window in service.get("headway_schedule", []):
            values.append(float(window.get("headway_s", 0)) / 60.0)
        out.setdefault(short, []).extend(v for v in values if v > 0)
    return out


def _label(ratio: float) -> str:
    if 0.8 <= ratio <= 1.25:
        return "realistic"
    return "proxy_denser_than_real" if ratio < 0.8 else "proxy_sparser_than_real"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gtfs", type=Path, default=ROOT / ".tools" / "stcp-gtfs" / "gtfs_feed.zip")
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "sumo_scenario_base.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "v3_stcp_gtfs_check.json")
    args = parser.parse_args()

    if not args.gtfs.exists():
        raise SystemExit(
            f"STCP GTFS not found at {args.gtfs}. Fetch it first:\n"
            f"  .venv/bin/python scripts/fetch_stcp_gtfs.py"
        )

    provenance_path = args.gtfs.parent / "PROVENANCE.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}

    real = extract_corridor_headways(str(args.gtfs), CORRIDOR_LINES)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    proxy = _proxy_headways_min_by_line(config)

    comparisons = []
    sanity_pass = True
    for short in MODELLED_LINES:
        line = real["lines"].get(short, {})
        am_medians = [
            d["windows"]["am_peak"]["median_headway_min"]
            for d in line.get("directions", {}).values()
            if d["windows"].get("am_peak") and "median_headway_min" in d["windows"]["am_peak"]
        ]
        proxy_values = sorted(set(round(v, 1) for v in proxy.get(short, [])))
        if not am_medians or not proxy_values:
            # A modelled line with no real medians or no proxy headways was NOT validated;
            # that must fail, not silently pass on the other lines.
            sanity_pass = False
            comparisons.append({"line": short, "status": "insufficient_data",
                                "real_am_medians_min": am_medians, "proxy_headways_min": proxy_values})
            continue
        real_am_median = round(median(am_medians), 2)
        proxy_min = min(proxy_values)
        ratio = round(proxy_min / real_am_median, 3)
        within = SANITY_BAND[0] <= ratio <= SANITY_BAND[1]
        sanity_pass = sanity_pass and within
        comparisons.append({
            "line": short,
            "real_am_peak_median_headway_min_by_dir": am_medians,
            "real_am_peak_median_headway_min": real_am_median,
            "proxy_headways_min": proxy_values,
            "proxy_densest_headway_min": proxy_min,
            "ratio_proxy_over_real": ratio,
            "within_sanity_band_0p5_2x": within,
            "descriptive_label": _label(ratio),
        })

    report = {
        "validation_phase": "V3_stcp_gtfs_pt_validation",
        "source_of_truth": provenance,
        "real_gtfs_extract": real,
        "proxy_headways_min_by_line": {k: sorted(set(round(v, 1) for v in vs)) for k, vs in proxy.items()},
        "comparison": comparisons,
        "honest_notes": [
            f"GTFS dwell encoded: {real['dwell_encoded_in_gtfs']} — STCP stop_times have "
            "departure==arrival, so the proxy dwell (~20 s) cannot be validated from GTFS.",
            "Line 204 is present in the real feed and runs the corridor but is not in "
            "public_transport.lines; candidate to add when geometry is real (V4).",
            "Proxy services use sim-relative seconds with no stated wall-clock anchor; the "
            "densest proxy headway is compared to the real AM-peak window.",
            "The real stop -> synthetic edge mapping is NOT regenerated here; that needs real "
            "geometry (V4). This phase validates headway realism only.",
            "within_sanity_band is an order-of-magnitude check (0.5x-2x), not a published "
            "calibration standard.",
        ],
        "verdict": "pass" if sanity_pass else "review",
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"STCP GTFS V3 — {provenance.get('snapshot_label', '?')} (service {real['service_id']})")
    for c in comparisons:
        if c.get("status") == "insufficient_data":
            print(f"  line {c['line']}: insufficient data")
        else:
            print(f"  line {c['line']}: real AM ~{c['real_am_peak_median_headway_min']}min | "
                  f"proxy {c['proxy_densest_headway_min']}min | ratio {c['ratio_proxy_over_real']} | "
                  f"{c['descriptive_label']}")
    print(f"  dwell in GTFS: {real['dwell_encoded_in_gtfs']}   verdict: {report['verdict']}   -> {args.out}")


if __name__ == "__main__":
    main()
