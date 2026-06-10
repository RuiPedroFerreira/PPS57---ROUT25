#!/usr/bin/env python3
"""Fetch the RESCO 'cologne8' reference network — pinned, not vendored.

The RESCO benchmark (via the MIT-licensed sumo-rl package) ships real-geometry
traffic-signal-control scenarios. 'cologne8' is an 8-intersection slice of the
real Cologne network (TAPASCologne lineage), used here as an independent
real-network target for the TSP method port (see docs/resco_sumo_rl_benchmark.md).

This script downloads only the network file at a pinned commit and records its
provenance + SHA-256. Nothing is vendored into this repository: the file lands in
a git-ignored directory (.tools/resco-cologne8/) at build time.

Licensing (recorded, not redistributed here):
  - sumo-rl package: MIT (https://github.com/LucasAlegre/sumo-rl)
  - cologne8 network data: Creative Commons (a 'Creative Commons Legal Code.pdf'
    ships next to the file in sumo-rl; TAPASCologne/OSM lineage).

Source of truth (pinned):
  repo   https://github.com/LucasAlegre/sumo-rl
  tag    v1.4.5
  commit f2d1d871ca79febb9a60b2b64fec2f9c6470e129
  path   sumo_rl/nets/RESCO/cologne8/cologne8.net.xml
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

REPO = "https://github.com/LucasAlegre/sumo-rl"
TAG = "v1.4.5"
COMMIT_SHA = "f2d1d871ca79febb9a60b2b64fec2f9c6470e129"
NET_REPO_PATH = "sumo_rl/nets/RESCO/cologne8/cologne8.net.xml"
PACKAGE_LICENSE = "MIT (sumo-rl)"
DATA_LICENSE = "Creative Commons (cologne8 data, TAPASCologne/OSM lineage)"
RAW_URL = f"https://raw.githubusercontent.com/LucasAlegre/sumo-rl/{COMMIT_SHA}/{NET_REPO_PATH}"

# SHA-256 of cologne8.net.xml at the pinned commit, computed from the real
# artifact on first fetch and baked in as an integrity pin.
EXPECTED_SHA256 = "51194519fd4d69726612d38b44606637c31389b86079f909139ba8f1aaffd2a8"

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    net_path = out_dir / "cologne8.net.xml"
    if not net_path.exists():
        if shutil.which("curl") is None:
            raise SystemExit("curl is required to fetch the pinned cologne8 network")
        print(f"Downloading {RAW_URL}")
        # Download to a temp file and rename (atomic) only after the transfer completes,
        # so an interrupted download never leaves a partial file at the final path.
        tmp = net_path.with_suffix(".part")
        subprocess.run(["curl", "-fsSL", "-o", str(tmp), RAW_URL], check=True)
        tmp.replace(net_path)
    else:
        print(f"Reusing cached {net_path}")

    sha = _sha256(net_path)
    size = net_path.stat().st_size
    if EXPECTED_SHA256 and sha != EXPECTED_SHA256:
        net_path.unlink(missing_ok=True)
        raise SystemExit(f"SHA-256 mismatch for {NET_REPO_PATH}: got {sha}, expected {EXPECTED_SHA256}")

    provenance = {
        "scenario": "RESCO cologne8 (8-intersection real Cologne slice)",
        "repo": REPO,
        "tag": TAG,
        "commit_sha": COMMIT_SHA,
        "repo_path": NET_REPO_PATH,
        "raw_url": RAW_URL,
        "package_license": PACKAGE_LICENSE,
        "data_license": DATA_LICENSE,
        "sha256": sha,
        "bytes": size,
        "vendored": False,
        "note": "Downloaded at build time into a git-ignored dir; not redistributed in this repo.",
    }
    (out_dir / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2, ensure_ascii=False))
    return net_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".tools" / "resco-cologne8")
    args = parser.parse_args()
    fetch(args.out_dir)


if __name__ == "__main__":
    main()
