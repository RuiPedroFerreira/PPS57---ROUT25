#!/usr/bin/env python3
"""Fetch real European urban traffic counts for V2 — not vendored.

The sim-to-real plan's V2 layer needs *real* traffic counts to judge whether the
modelled Boavista demand is plausible. Porto publishes none openly (the CMP
request was declined), so V2 transfers a reference envelope from other European
cities. This script downloads those real, openly-licensed datasets, records full
provenance (URL, SHA-256, fetch time, and — for the live feed — the source's own
timestamp) and drops the raw payloads into a git-ignored directory
(.tools/reference-counts/). Only the derived validation report is committed.

It invents nothing: if a source cannot be reached or returns no usable rows it
fails loudly rather than substituting defaults.

Sources
  * Madrid — Ayuntamiento de Madrid open data (datos.madrid.es):
      - real-time intensity feed informo/tmadrid/pm.xml (intensidad in veh/h,
        error validity flag) — the actual measured flows;
      - measurement-point catalogue CSV (tipo_elem URB/M30) — to keep only urban
        detectors. The portal publishes one catalogue snapshot per month, so the
        current resource is resolved from its CKAN API at fetch time (a pinned
        resource id goes stale: the 2019 snapshot silently lost ~20% of the live
        feed's detectors) and the feed↔catalogue join coverage is checked before
        anything is written. Live feed: SHA-256 is recorded, not pinned (it
        changes ~every 5 min); the committed report names the exact snapshot used.
  * United Kingdom — Department for Transport road traffic statistics open API
    (roadtraffic.dft.gov.uk), AADF per count point/direction for a set of city
    local authorities. Open Government Licence v3.0. A given survey year is
    stable, so its SHA-256 pins the snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation import reference_counts as rc  # noqa: E402

OUT_DIR = ROOT / ".tools" / "reference-counts"
USER_AGENT = "PPS57-ROUT25-validation/1.0 (research; sim-to-real V2)"

MADRID_INTENSITY_URL = "https://informo.madrid.es/informo/tmadrid/pm.xml"
# Measurement-point catalogue dataset ("Tráfico. Ubicación de los puntos de
# medida del tráfico"): one CSV location snapshot per month. The current
# resource is resolved from this CKAN endpoint at fetch time.
MADRID_CATALOGUE_DATASET_API = (
    "https://datos.madrid.es/api/3/action/package_show?id=202468-0-intensidad-trafico"
)
# Detectors absent from the catalogue are silently treated as non-URB and
# dropped, so a join this poor means the catalogue no longer describes the feed.
MADRID_MIN_CATALOGUE_COVERAGE = 0.95
MADRID_LICENSE = "Ayuntamiento de Madrid open data conditions (datos.madrid.es)"

DFT_API = "https://roadtraffic.dft.gov.uk/api"
DFT_LICENSE = "Open Government Licence v3.0 (UK Department for Transport)"
DEFAULT_DFT_CITIES = [
    "Glasgow City",
    "City of Edinburgh",
    "Birmingham",
    "Leeds",
    "Manchester",
    "Bristol, City of",
]
DEFAULT_DFT_YEAR = 2024


def _sha256_bytes(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _get(url: str, *, timeout: int = 60) -> bytes:
    """HTTPS GET via curl (uses system CA store; the repo's fetchers all use curl).

    macOS' framework Python ships without a usable CA bundle, so urllib raises
    CERTIFICATE_VERIFY_FAILED; curl resolves it with the OS trust store.
    """
    if shutil.which("curl") is None:
        raise SystemExit("curl is required to fetch reference counts (not found on PATH).")
    proc = subprocess.run(
        ["curl", "-sS", "-L", "--fail", "--max-time", str(timeout), "-A", USER_AGENT, url],
        capture_output=True,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:] or [""]
        raise SystemExit(f"Fetch failed ({url}): {tail[0]}")
    return proc.stdout


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _madrid_source_timestamp(text: str) -> str | None:
    start = text.find("<fecha_hora>")
    end = text.find("</fecha_hora>")
    if start != -1 and end != -1:
        return text[start + len("<fecha_hora>") : end].strip()
    return None


def resolve_madrid_catalogue_resource() -> dict:
    """The newest monthly measurement-point catalogue resource from the portal.

    The dataset republishes the point locations every month; resolving the
    current resource (instead of pinning one id) keeps detectors added or
    renumbered since any given snapshot. Which exact resource was used goes
    into provenance.
    """
    payload = json.loads(_get(MADRID_CATALOGUE_DATASET_API))
    if not payload.get("success"):
        raise SystemExit("datos.madrid.es package_show returned success=false — cannot resolve the catalogue.")
    resources = (payload.get("result") or {}).get("resources") or []
    # Every CSV in this dataset is a monthly point-location snapshot (ZIP/XLSX are
    # the same data in other formats), but only recent months carry the
    # pmed_ubicacion_MM-YYYY name — older snapshots have opaque ids. Select by
    # format + created instead of filename; the join-coverage gate below fails
    # loudly if the portal ever publishes a CSV that is not a usable catalogue.
    candidates = [res for res in resources if str(res.get("format", "")).upper() == "CSV"]
    if not candidates:
        raise SystemExit(
            "No CSV catalogue resource found in the datos.madrid.es measurement-points dataset — "
            "refusing to fall back to a stale pinned snapshot."
        )
    newest = max(candidates, key=lambda res: str(res.get("created", "")))
    return {"url": str(newest["url"]), "created": str(newest.get("created", "")), "id": str(newest.get("id", ""))}


def fetch_madrid(out_dir: Path, catalogue_url: str | None = None) -> dict:
    intensity = _get(MADRID_INTENSITY_URL)
    if b"<intensidad>" not in intensity:
        raise SystemExit("Madrid feed returned no <intensidad> elements — refusing to fabricate.")
    if catalogue_url:
        resource = {"url": catalogue_url, "created": None, "id": None, "note": "overridden via --madrid-catalogue-url"}
    else:
        resource = resolve_madrid_catalogue_resource()
    catalogue = _get(resource["url"])
    intensity_text = intensity.decode("utf-8", errors="replace")
    # The catalogue must actually describe the feed: detectors missing from it are
    # silently dropped by only_urban filtering, so a poor join is a stale snapshot.
    coverage = rc.madrid_feed_catalogue_coverage(
        intensity_text,
        rc.parse_madrid_catalogue(catalogue.decode("utf-8", errors="replace")),
    )
    if coverage["coverage"] is None or coverage["coverage"] < MADRID_MIN_CATALOGUE_COVERAGE:
        raise SystemExit(
            f"Madrid catalogue covers only {coverage['in_catalogue']}/{coverage['feed_valid_detectors']} "
            f"valid feed detectors (coverage {coverage['coverage']}, minimum {MADRID_MIN_CATALOGUE_COVERAGE}). "
            f"Stale or wrong catalogue resource ({resource['url']}) — refusing to drop readings silently."
        )
    # Write only after validation, so a bad fetch can never leave raw files on disk
    # that disagree with (or lack) provenance.
    (out_dir / "madrid_pm.xml").write_bytes(intensity)
    (out_dir / "madrid_catalogue.csv").write_bytes(catalogue)
    return {
        "source": "datos.madrid.es",
        "license": MADRID_LICENSE,
        "fetched_at_utc": _now_iso(),
        "feed_timestamp": _madrid_source_timestamp(intensity_text),
        "intensity_url": MADRID_INTENSITY_URL,
        "intensity_sha256": _sha256_bytes(intensity),
        "intensity_bytes": len(intensity),
        "catalogue_url": resource["url"],
        "catalogue_resource": {k: v for k, v in resource.items() if k != "url"},
        "catalogue_sha256": _sha256_bytes(catalogue),
        "catalogue_bytes": len(catalogue),
        "feed_catalogue_coverage": coverage,
        "sha256_note": "live feed — recorded, not pinned; the committed report names this snapshot.",
    }


def _resolve_dft_city_ids(cities: list[str]) -> dict[str, int]:
    raw = _get(f"{DFT_API}/local-authorities")
    table = json.loads(raw)
    by_name = {str(row["name"]).strip().lower(): int(row["id"]) for row in table}
    resolved: dict[str, int] = {}
    for city in cities:
        key = city.strip().lower()
        if key in by_name:
            resolved[city] = by_name[key]
        else:
            print(f"  [warn] DfT local authority not found, skipping: {city}", file=sys.stderr)
    if not resolved:
        raise SystemExit("No DfT local authorities resolved — refusing to fabricate.")
    return resolved


def _fetch_dft_la_year(la_id: int, year: int, *, page_size: int = 200) -> list[dict]:
    records: list[dict] = []
    page = 1
    while True:
        params = urllib.parse.urlencode(
            {
                "filter[local_authority_id]": la_id,
                "filter[year]": year,
                "page[size]": page_size,
                "page[number]": page,
            }
        )
        payload = json.loads(_get(f"{DFT_API}/average-annual-daily-flow-by-direction?{params}"))
        rows = payload.get("data", [])
        records.extend(rows)
        last_page = int(payload.get("last_page", page))
        if page >= last_page or not rows:
            break
        page += 1
    return records


def fetch_dft(out_dir: Path, cities: list[str], year: int) -> dict:
    ids = _resolve_dft_city_ids(cities)
    all_records: list[dict] = []
    per_city: dict[str, int] = {}
    for city, la_id in ids.items():
        rows = _fetch_dft_la_year(la_id, year)
        per_city[city] = len(rows)
        all_records.extend(rows)
    if not all_records:
        raise SystemExit(f"DfT returned no AADF rows for {year} — refusing to fabricate.")
    blob = json.dumps(all_records, sort_keys=True).encode("utf-8")
    (out_dir / "dft_aadf.json").write_bytes(blob)
    return {
        "source": "roadtraffic.dft.gov.uk",
        "license": DFT_LICENSE,
        "fetched_at_utc": _now_iso(),
        "year": year,
        "endpoint": f"{DFT_API}/average-annual-daily-flow-by-direction",
        "local_authorities": ids,
        "records_per_city": per_city,
        "records_total": len(all_records),
        "sha256": _sha256_bytes(blob),
        "bytes": len(blob),
    }


def load_existing_provenance(path: Path) -> dict | None:
    """Existing provenance.json, or None when absent. Corrupt JSON fails loudly."""
    if not path.exists():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Existing provenance is not valid JSON ({path}): {exc}. "
            "Delete it and re-fetch ALL sources rather than overwriting silently."
        )
    if not isinstance(existing, dict):
        raise SystemExit(f"Existing provenance has unexpected shape ({path}): expected an object.")
    return existing


def merge_provenance(existing: dict | None, fetched_sources: dict, *, fetched_at_utc: str) -> dict:
    """Merge this run's fetched source sections into the existing provenance.

    Sources skipped this run (e.g. --skip-madrid) keep their previously recorded
    section, so provenance.json always describes every raw file on disk instead
    of only the last run's subset.
    """
    sources: dict = {}
    if existing:
        sources.update(existing.get("sources", {}))
    sources.update(fetched_sources)
    return {"fetched_at_utc": fetched_at_utc, "sources": sources}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--madrid-catalogue-url",
        default=None,
        help="pin a specific catalogue resource URL instead of resolving the newest monthly snapshot",
    )
    parser.add_argument("--dft-cities", nargs="*", default=DEFAULT_DFT_CITIES)
    parser.add_argument("--dft-year", type=int, default=DEFAULT_DFT_YEAR)
    parser.add_argument("--skip-madrid", action="store_true")
    parser.add_argument("--skip-dft", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    fetched_sources: dict = {}

    if not args.skip_madrid:
        print("Fetching Madrid (informo intensity + measurement-point catalogue)…")
        fetched_sources["madrid"] = fetch_madrid(args.out, args.madrid_catalogue_url)
        m = fetched_sources["madrid"]
        cov = m["feed_catalogue_coverage"]
        print(f"  madrid: feed {m['feed_timestamp']}  {m['intensity_bytes']} B  sha {m['intensity_sha256'][:12]}…")
        print(f"  madrid catalogue: {m['catalogue_url'].rsplit('/', 1)[-1]}  "
              f"join {cov['in_catalogue']}/{cov['feed_valid_detectors']} detectors (coverage {cov['coverage']})")
    if not args.skip_dft:
        print(f"Fetching UK DfT AADF (year {args.dft_year}) for {len(args.dft_cities)} cities…")
        fetched_sources["dft"] = fetch_dft(args.out, args.dft_cities, args.dft_year)
        d = fetched_sources["dft"]
        print(f"  dft: {d['records_total']} rows across {len(d['local_authorities'])} LAs  sha {d['sha256'][:12]}…")

    existing = load_existing_provenance(args.out / "provenance.json")
    provenance = merge_provenance(existing, fetched_sources, fetched_at_utc=_now_iso())
    (args.out / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Provenance → {args.out / 'provenance.json'}")


if __name__ == "__main__":
    main()
