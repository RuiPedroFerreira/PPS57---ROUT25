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


def _is_valid_osm(data: bytes) -> bool:
    """True only for a COMPLETE Overpass response that carries real road data.

    The tail `</osm>` confirms the download finished, but Overpass delivers
    runtime/timeout errors (HTTP 200, body `<remark>...</remark>`) and empty
    result sets (`<osm></osm>`) as well-formed XML that still ends in `</osm>`.
    Require at least one `<way ` and reject the `<remark>` error marker, so an
    error/empty body is never published nor reused as a valid cache.
    """
    return b"</osm>" in data[-512:] and b"<way " in data and b"<remark>" not in data


def _download(out: Path) -> None:
    if shutil.which("curl") is None:
        raise SystemExit("curl is required to fetch OSM data")
    # Download to a temp file and publish (rename) only a COMPLETE response, so a transient
    # Overpass error/partial body never persists as a bad cache that the next run reuses.
    tmp = out.with_suffix(".part")
    for endpoint in ENDPOINTS:
        print(f"Querying Overpass: {endpoint}")
        # --fail: without it curl exits 0 on HTTP errors, saving the error body as if it
        # were data, and --retry-all-errors never sees HTTP-level errors to retry.
        result = subprocess.run(
            ["curl", "-sS", "--fail", "--max-time", "90", "--retry", "2", "--retry-all-errors",
             "-A", USER_AGENT, "-o", str(tmp), endpoint, "--data-urlencode", f"data={OVERPASS_QUERY}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and tmp.exists() and _is_valid_osm(tmp.read_bytes()):
            tmp.replace(out)
            return
    tmp.unlink(missing_ok=True)
    raise SystemExit("Overpass fetch failed on all endpoints (try again later; servers can be busy)")


def fetch(out_dir: Path, allow_drift: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    osm_path = out_dir / "boavista.osm.xml"
    # Re-download if the cache is missing OR not a complete OSM response: a previous
    # transient failure must not be reused as a valid extract.
    if not osm_path.exists() or not _is_valid_osm(osm_path.read_bytes()):
        _download(osm_path)
    else:
        print(f"Reusing cached {osm_path}")

    sha = _sha256(osm_path)
    if EXPECTED_SHA256 and sha != EXPECTED_SHA256:
        # OSM is a live database and public Overpass instances reject the dated/attic
        # queries that would pin exact bytes, so the extract drifts over time. Fail by
        # default (the committed report was produced from the pinned snapshot); --allow-drift
        # reproduces the pipeline on the current geometry (then update EXPECTED_SHA256 to re-pin).
        message = (
            f"OSM snapshot sha256 {sha} != pinned {EXPECTED_SHA256}: the live Boavista extract "
            "has drifted since the committed report was produced."
        )
        if not allow_drift:
            raise SystemExit(
                message + " Re-run with --allow-drift to reproduce on the current geometry, "
                "then update EXPECTED_SHA256 to re-pin."
            )
        print("WARNING: " + message + " Proceeding (--allow-drift); update EXPECTED_SHA256 to re-pin.")

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
    parser.add_argument("--allow-drift", action="store_true",
                        help="proceed if the live OSM extract no longer matches the pinned sha "
                             "(reproduce the pipeline on the current Boavista geometry)")
    args = parser.parse_args()
    fetch(args.out_dir, allow_drift=args.allow_drift)


if __name__ == "__main__":
    main()
