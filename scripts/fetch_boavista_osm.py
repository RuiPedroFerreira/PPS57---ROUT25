#!/usr/bin/env python3
"""Fetch the real Avenida da Boavista corridor from OpenStreetMap — not vendored.

V4 of the sim-to-real validation plan replaces the synthetic corridor geometry
with the real Boavista road network from OSM (the source of truth). This script
queries Overpass for the drivable network inside a bounding box derived from the
corridor's real endpoints, then records provenance + SHA-256. OSM is ODbL; the
raw extract lands in a git-ignored directory (.tools/boavista-osm/) — only our
derived validation report is committed.

Bounding box: derived from Nominatim-sourced corridor endpoints
  Rotunda da Boavista  41.1579, -8.6291   (east end, by Casa da Musica)
  Castelo do Queijo    41.1685, -8.6900   (west end, at the coast)
expanded with a small margin -> (S, W, N, E) = (41.156, -8.692, 41.170, -8.627).
OSM is a live database; the SHA-256 below pins the exact snapshot we used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

BBOX_S, BBOX_W, BBOX_N, BBOX_E = 41.156, -8.692, 41.170, -8.627
DRIVABLE = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
    "living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)
OVERPASS_QUERY = (
    f'[out:xml][timeout:60];(way["highway"~"^({DRIVABLE})$"]'
    f"({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});>;);out body;"
)
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
USER_AGENT = "PPS57-ROUT25-validation/1.0 (research)"
LICENSE = "ODbL (OpenStreetMap contributors)"

# SHA-256 of the OSM extract, computed from the real snapshot on first fetch and
# baked in as an integrity pin. OSM is live, so a re-fetch may differ; this pins
# exactly the snapshot the committed validation report was produced from.
EXPECTED_SHA256 = "012ac5593ce72b131944ad368a41bb48f517387fb4c62d136045ff9a9cffce99"

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(out: Path) -> None:
    if shutil.which("curl") is None:
        raise SystemExit("curl is required to fetch OSM data")
    for endpoint in ENDPOINTS:
        print(f"Querying Overpass: {endpoint}")
        result = subprocess.run(
            ["curl", "-sS", "--max-time", "90", "-A", USER_AGENT,
             "-o", str(out), endpoint, "--data-urlencode", f"data={OVERPASS_QUERY}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and out.exists() and b"</osm>" in out.read_bytes()[-256:]:
            return
    raise SystemExit("Overpass fetch failed on all endpoints (try again later; servers can be busy)")


def fetch(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    osm_path = out_dir / "boavista.osm.xml"
    if not osm_path.exists():
        _download(osm_path)
    else:
        print(f"Reusing cached {osm_path}")

    sha = _sha256(osm_path)
    if EXPECTED_SHA256 and sha != EXPECTED_SHA256:
        # Fail (don't just warn): the committed report was produced from the pinned
        # snapshot. OSM is live, so if the change is intended, delete the cached extract
        # and update EXPECTED_SHA256 to re-pin.
        raise SystemExit(
            f"OSM snapshot sha256 {sha} != pinned {EXPECTED_SHA256}. The pinned Boavista "
            "extract changed (OSM is live); re-pin EXPECTED_SHA256 if this is intended."
        )

    provenance = {
        "source": "OpenStreetMap via Overpass API",
        "bbox_S_W_N_E": [BBOX_S, BBOX_W, BBOX_N, BBOX_E],
        "bbox_origin": "Nominatim-sourced corridor endpoints (Rotunda da Boavista, Castelo do Queijo) + margin",
        "overpass_query": OVERPASS_QUERY,
        "license": LICENSE,
        "sha256": sha,
        "bytes": osm_path.stat().st_size,
        "vendored": False,
        "note": "OSM is a live database; sha256 pins the exact snapshot used. Not redistributed in this repo.",
    }
    (out_dir / "OSM_PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2, ensure_ascii=False))
    return osm_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".tools" / "boavista-osm")
    args = parser.parse_args()
    fetch(args.out_dir)


if __name__ == "__main__":
    main()
