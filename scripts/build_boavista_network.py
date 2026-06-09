#!/usr/bin/env python3
"""Convert the real Boavista OSM extract into a SUMO network (V4).

Runs netconvert with a documented OSM-import recipe and records the exact options,
netconvert version and output SHA-256 as provenance. Consumes the OSM extract from
scripts/fetch_boavista_osm.py; writes the net into the git-ignored .tools dir
(OSM-derived, ODbL — not vendored).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.environment import resolve_sumo_home  # noqa: E402

# Documented OSM-import recipe: drivable car network, OSM traffic_signals -> static
# TLS, joined junctions/TLS. Real signal *locations* (from OSM), netconvert-default
# *timings* (real signal plans are not openly available — an honest V4 limit).
NETCONVERT_OPTIONS = [
    "--geometry.remove", "--roundabouts.guess", "--ramps.guess",
    "--junctions.join", "--tls.guess-signals", "--tls.discard-simple", "--tls.join",
    "--keep-edges.by-vclass", "passenger", "--remove-edges.isolated",
    "--no-turnarounds.tls", "--tls.default-type", "static", "--no-warnings", "true",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_typemap() -> Path:
    # Resolve via SUMO_HOME first (handles system-wide / SUMO_HOME installs where SUMO
    # is on PATH but not inside this repo's .venv); fall back to a search.
    home = resolve_sumo_home()
    if home is not None:
        candidate = home / "data" / "typemap" / "osmNetconvert.typ.xml"
        if candidate.exists():
            return candidate
    search_roots = [ROOT / ".venv"] + ([home] if home is not None else [])
    for root in search_roots:
        hits = list(root.rglob("osmNetconvert.typ.xml"))
        if hits:
            return hits[0]
    raise SystemExit("osmNetconvert.typ.xml not found (is SUMO installed / SUMO_HOME set?).")


def build(osm_path: Path, out_dir: Path) -> Path:
    if not osm_path.exists():
        raise SystemExit(f"OSM extract not found at {osm_path}. Run scripts/fetch_boavista_osm.py first.")
    if shutil.which("netconvert") is None:
        raise SystemExit("netconvert not found on PATH (activate .venv or install SUMO).")

    net_path = out_dir / "boavista.net.xml"
    typemap = _find_typemap()
    version = subprocess.run(["netconvert", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    cmd = ["netconvert", "--osm-files", str(osm_path), "-t", str(typemap),
           "-o", str(net_path), *NETCONVERT_OPTIONS]
    subprocess.run(cmd, check=True)

    provenance = {
        "tool": version,
        "typemap": str(typemap.name),
        "options": " ".join(NETCONVERT_OPTIONS),
        "input_osm": str(osm_path),
        "net_sha256": _sha256(net_path),
        "note": "OSM traffic_signals -> static TLS (real signal LOCATIONS, netconvert-default TIMINGS; "
                "real signal plans are not openly available).",
    }
    (out_dir / "NET_PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # run_reference_network_check.py reads PROVENANCE.json beside the net by default;
    # write the combined OSM+net provenance there so the documented fetch->build->check
    # sequence yields the full source_of_truth instead of an empty {}.
    osm_prov_path = out_dir / "OSM_PROVENANCE.json"
    combined = {
        "phase_input": "real OSM Boavista geometry (V4)",
        "osm": json.loads(osm_prov_path.read_text(encoding="utf-8")) if osm_prov_path.exists() else {},
        "net": provenance,
    }
    (out_dir / "PROVENANCE.json").write_text(
        json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2, ensure_ascii=False))
    return net_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--osm", type=Path, default=ROOT / ".tools" / "boavista-osm" / "boavista.osm.xml")
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".tools" / "boavista-osm")
    args = parser.parse_args()
    build(args.osm, args.out_dir)


if __name__ == "__main__":
    main()
