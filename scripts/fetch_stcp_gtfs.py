#!/usr/bin/env python3
"""Fetch the STCP GTFS feed (Porto public transport) — pinned, not vendored.

V3 of the sim-to-real validation plan replaces/validates the proxy public-transport
assumptions with the real STCP timetable. The feed is published CC0 on the Porto
Open Data portal; this script downloads a pinned dated snapshot and records its
provenance + SHA-256. The zip lands in a git-ignored directory (.tools/stcp-gtfs/);
only our derived validation report is committed.

Source of truth (pinned):
  portal    https://opendata.porto.digital  (dataset "Horarios, paragens e rotas da STCP")
  dataset   5275c986-592c-43f5-8f87-aabbd4e4f3a4
  resource  c71a52cd-b4ed-4011-896a-898e92b15a6f  ("GTFS STCP 05-06-2026 Mais Recente")
  license   Creative Commons CC0
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

PORTAL = "https://opendata.porto.digital"
DATASET_ID = "5275c986-592c-43f5-8f87-aabbd4e4f3a4"
RESOURCE_ID = "c71a52cd-b4ed-4011-896a-898e92b15a6f"
SNAPSHOT_LABEL = "GTFS STCP 05-06-2026 Mais Recente"
LICENSE = "Creative Commons CC0"
RAW_URL = f"{PORTAL}/dataset/{DATASET_ID}/resource/{RESOURCE_ID}/download/gtfs_feed.zip"

# SHA-256 of the pinned GTFS zip, computed from the real artifact on first fetch
# and baked in as an integrity pin.
EXPECTED_SHA256 = "48870fdaf236e473173d1c6a576f305220fb723de6e6f7850eea8821ef5c0669"

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "gtfs_feed.zip"
    if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        if shutil.which("curl") is None:
            raise SystemExit("curl is required to fetch the pinned STCP GTFS feed")
        print(f"Downloading {RAW_URL}")
        tmp = zip_path.with_suffix(".part")
        # Browser-like UA + retries: the portal can answer a bare curl UA with HTTP 403,
        # and transient errors should retry. Download to a temp file and rename only after
        # validating it is a real zip, so a failed/partial fetch never leaves a bad cache.
        subprocess.run(
            ["curl", "-fsSL", "--retry", "3", "--retry-delay", "2", "--retry-all-errors",
             "-A", "Mozilla/5.0 (PPS57-ROUT25-validation)", "-o", str(tmp), RAW_URL],
            check=True,
        )
        if not zipfile.is_zipfile(tmp):
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"Downloaded STCP GTFS is not a valid zip (portal error?): {RAW_URL}")
        tmp.replace(zip_path)
    else:
        print(f"Reusing cached {zip_path}")

    sha = _sha256(zip_path)
    size = zip_path.stat().st_size
    if EXPECTED_SHA256 and sha != EXPECTED_SHA256:
        zip_path.unlink(missing_ok=True)
        raise SystemExit(f"SHA-256 mismatch for STCP GTFS: got {sha}, expected {EXPECTED_SHA256}")

    provenance = {
        "feed": "STCP GTFS (Porto)",
        "portal": PORTAL,
        "dataset_id": DATASET_ID,
        "resource_id": RESOURCE_ID,
        "snapshot_label": SNAPSHOT_LABEL,
        "raw_url": RAW_URL,
        "license": LICENSE,
        "sha256": sha,
        "bytes": size,
        "vendored": False,
        "note": "Downloaded at build time into a git-ignored dir; not redistributed in this repo.",
    }
    (out_dir / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2, ensure_ascii=False))
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".tools" / "stcp-gtfs")
    args = parser.parse_args()
    fetch(args.out_dir)


if __name__ == "__main__":
    main()
