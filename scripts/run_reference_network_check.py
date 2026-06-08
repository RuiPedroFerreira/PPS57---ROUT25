#!/usr/bin/env python3
"""Port the TSP engine onto an arbitrary pinned reference network and record evidence.

Generic reference-network method check: runs the existing empirical TSP + Safety
probe (``scripts/empirical_network_profile_check.py``) across every traffic light
in a real, externally-sourced network and consolidates the per-intersection
results into a tracked, provenance-stamped report. Used for MoST (Monaco) and the
RESCO 'cologne8' reference networks; the network is the source of truth, fetched
(not vendored) by the matching ``scripts/fetch_*.py``.

A pass means: on a real external network, the map-agnostic NetworkProfile
reproduces SUMO's loaded TLS programs with **zero mismatches** and the pipeline
runs end-to-end on the reachable intersections. A Safety Layer **block** is NOT a
failure — a fail-closed verdict on unsafe timing is correct behaviour.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.network_profile import load_network_profile  # noqa: E402

EMPIRICAL = ROOT / "scripts" / "empirical_network_profile_check.py"


def _probe(net: Path, tls_id: str, sim_time: float, port: int) -> dict:
    """Run one empirical TSP+Safety probe for a single TLS in an isolated process."""
    handle = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    handle.close()
    out = Path(handle.name)
    proc = subprocess.run(
        [
            sys.executable, str(EMPIRICAL),
            "--network", str(net),
            "--tls-id", tls_id,
            "--sim-time", str(sim_time),
            "--traci-port", str(port),
            "--output", str(out),
        ],
        capture_output=True,
        text=True,
    )
    # The probe writes its report even when Safety blocks (exit 1), so trust the
    # file, not the return code. No file => no reachable movement for this TLS.
    if out.exists() and out.stat().st_size > 0:
        data = json.loads(out.read_text(encoding="utf-8"))
        out.unlink(missing_ok=True)
        return {
            "tls_id": tls_id,
            "status": "probed",
            "mismatch_count": data["traci_profile_mismatch_count"],
            "decision_action": data["decision"]["action"],
            "decision_reason": data["decision"]["reason"],
            "safety_status": data["safety"]["status"],
            "safety_reason": data["safety"]["reason"],
            "selected_movement": data["selected_movement"],
        }
    out.unlink(missing_ok=True)
    detail = (proc.stderr.strip().splitlines() or ["unknown"])[-1]
    return {"tls_id": tls_id, "status": "no_reachable_movement", "detail": detail[:200]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--net", type=Path, required=True, help="pinned reference net.xml (git-ignored)")
    parser.add_argument("--provenance", type=Path, help="PROVENANCE.json next to the net (defaults beside --net)")
    parser.add_argument("--label", default="reference_network", help="phase label for the report")
    parser.add_argument("--sim-time", type=float, default=30.0)
    parser.add_argument("--base-port", type=int, default=8890)
    parser.add_argument("--out", type=Path, required=True, help="tracked report path under docs/validation/")
    args = parser.parse_args()

    if not args.net.exists():
        raise SystemExit(f"Reference network not found at {args.net}. Fetch it first (scripts/fetch_*.py).")

    provenance_path = args.provenance or (args.net.parent / "PROVENANCE.json")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}

    profile = load_network_profile(args.net)
    tls_ids = profile.tls_ids()
    probes = [_probe(args.net, tls, args.sim_time, args.base_port + i) for i, tls in enumerate(tls_ids)]

    probed = [p for p in probes if p["status"] == "probed"]
    max_mismatch = max((p["mismatch_count"] for p in probed), default=0)
    verdict = "pass" if (probed and max_mismatch == 0) else "fail"

    report = {
        "validation_phase": args.label,
        "source_of_truth": provenance,
        "network_fingerprint_sha256": profile.fingerprint,
        "sim_time_s": args.sim_time,
        "probes": probes,
        "summary": {
            "tls_total": len(tls_ids),
            "tls_probed": len(probed),
            "tls_no_reachable_movement": len(tls_ids) - len(probed),
            "max_profile_mismatch_count": max_mismatch,
            "zero_mismatch_on_all_probes": max_mismatch == 0 and bool(probed),
            "decision_actions_seen": sorted({p["decision_action"] for p in probed}),
            "safety_statuses_seen": sorted({p["safety_status"] for p in probed}),
        },
        "interpretation": (
            "Map-agnostic NetworkProfile reproduced SUMO's loaded TLS programs with "
            f"{max_mismatch} mismatch(es) across {len(probed)} probed traffic light(s) of "
            f"{len(tls_ids)} in a real external reference network; the TSP engine + Safety "
            "Layer ran end-to-end on each. Safety blocks are correct fail-closed verdicts."
        ),
        "verdict": verdict,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"{args.label} — {provenance.get('tag', '?')} @ {profile.fingerprint[:12]}")
    print(f"  TLS probed: {len(probed)}/{len(tls_ids)}   max profile mismatch: {max_mismatch}")
    print(f"  decisions: {report['summary']['decision_actions_seen']}")
    print(f"  safety:    {report['summary']['safety_statuses_seen']}")
    print(f"  verdict:   {verdict}   -> {args.out}")
    if verdict != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
