#!/usr/bin/env python3
"""Fetch the MoST (Monaco SUMO Traffic) reference network — pinned, not vendored.

V1 of the sim-to-real validation plan ports the TSP engine onto an external,
peer-reviewed, real-geometry reference scenario instead of the synthetic Porto
corridor. The MoST scenario is the source of truth; this script downloads only
the network file at a pinned commit and records its provenance + SHA-256.

MoST is GPL-3.0, so its files are NOT vendored into this repository: they are
downloaded into a git-ignored directory (.tools/most/) at build time. We commit
only our own derived validation report.

Source of truth (pinned):
  repo   https://github.com/lcodeca/MoSTScenario
  tag    v0.6
  commit 088f97195e9b4a7e2b17a46d7afd3f84e71c73a1
  path   scenario/in/most.net.xml
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

MOST_REPO = "https://github.com/lcodeca/MoSTScenario"
MOST_TAG = "v0.6"
MOST_COMMIT_SHA = "088f97195e9b4a7e2b17a46d7afd3f84e71c73a1"
MOST_NET_REPO_PATH = "scenario/in/most.net.xml"
MOST_LICENSE = "GPL-3.0"
RAW_URL = (
    f"https://raw.githubusercontent.com/lcodeca/MoSTScenario/"
    f"{MOST_COMMIT_SHA}/{MOST_NET_REPO_PATH}"
)

# SHA-256 of scenario/in/most.net.xml at the pinned commit. Computed from the
# real artifact on first fetch and baked in here as an integrity pin; the
# download is rejected if the bytes do not match.
EXPECTED_SHA256 = "cbbd424956caccdd2fa79c984f934d286176f11f6fa9e5fbbbc2320e30da9937"

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    net_path = out_dir / "most.net.xml"
    if not net_path.exists():
        if shutil.which("curl") is None:
            raise SystemExit("curl is required to fetch the pinned MoST network")
        print(f"Downloading {RAW_URL}")
        # curl uses the system trust store (the framework Python lacks a CA bundle).
        # Download to a temp file and rename (atomic) only after the transfer completes,
        # so an interrupted download never leaves a partial file at the final path.
        tmp = net_path.with_suffix(".part")
        subprocess.run(
            ["curl", "-fsSL", "-o", str(tmp), RAW_URL],
            check=True,
        )
        tmp.replace(net_path)
    else:
        print(f"Reusing cached {net_path}")

    sha = _sha256(net_path)
    size = net_path.stat().st_size
    if EXPECTED_SHA256 and sha != EXPECTED_SHA256:
        net_path.unlink(missing_ok=True)
        raise SystemExit(
            f"SHA-256 mismatch for {MOST_NET_REPO_PATH}: got {sha}, expected {EXPECTED_SHA256}"
        )

    provenance = {
        "scenario": "MoST (Monaco SUMO Traffic)",
        "repo": MOST_REPO,
        "tag": MOST_TAG,
        "commit_sha": MOST_COMMIT_SHA,
        "repo_path": MOST_NET_REPO_PATH,
        "raw_url": RAW_URL,
        "license": MOST_LICENSE,
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
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".tools" / "most")
    args = parser.parse_args()
    fetch(args.out_dir)


if __name__ == "__main__":
    main()
