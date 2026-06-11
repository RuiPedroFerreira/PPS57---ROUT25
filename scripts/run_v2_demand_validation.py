#!/usr/bin/env python3
"""V2: validate the modelled corridor demand against a real European envelope.

Closes the V2 layer of the sim-to-real plan honestly. Porto has no open traffic
counts, so instead of a (impossible) Porto GEH calibration this transfers a
reference envelope from real, openly-published European city counts and checks
that the modelled Boavista arterial intensity falls inside the spread those real
cities exhibit. It is a face-validity gate (a transfer), NOT a Porto calibration —
the report says so, and every reference number traces to a fetched dataset.

Inputs (produced by scripts/fetch_reference_counts.py into .tools/reference-counts/):
  * madrid_pm.xml + madrid_catalogue.csv  — measured veh/h per urban (URB) detector
  * dft_aadf.json                          — UK DfT AADF per count point/direction
  * provenance.json                        — URLs, SHA-256, timestamps, licences

The modelled corridor intensity comes from the V4d evidence produced by
scripts/build_reference_corridor.py (default path
docs/validation/v4d_reference_corridor.json :: demand.arterial_intensity_measured;
the file is generated locally, not committed). V2 itself re-runs no SUMO.

Run scripts/fetch_reference_counts.py (reference counts) and
scripts/build_reference_corridor.py (V4d evidence) first, or pass --v4d;
this script refuses to invent data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation import reference_counts as rc  # noqa: E402
from pps57_sumo.validation.acceptance import load_validation_config  # noqa: E402

RAW_DIR = ROOT / ".tools" / "reference-counts"


def _require(path: Path, hint: str = "Run scripts/fetch_reference_counts.py first") -> Path:
    if not path.exists():
        raise SystemExit(f"Missing {path}. {hint} (no data is invented).")
    return path


def provenance_source(provenance: Mapping, name: str) -> Mapping:
    """The recorded provenance section for ``name``; fails loudly when absent."""
    sources = provenance.get("sources") or {}
    section = sources.get(name)
    if not isinstance(section, Mapping):
        raise SystemExit(
            f"provenance.json has no '{name}' source section (recorded sources: {sorted(sources)}). "
            f"Re-run scripts/fetch_reference_counts.py without --skip-{name} so every reference is traceable."
        )
    return section


def verify_raw_hashes(raw_dir: Path, provenance: Mapping) -> list[dict]:
    """Recompute each raw file's SHA-256 and refuse to run on mismatch with provenance.

    The committed report embeds provenance.json; this check guarantees the raw
    payloads actually parsed are the exact bytes that provenance describes.
    """
    madrid = provenance_source(provenance, "madrid")
    dft = provenance_source(provenance, "dft")
    expected = [
        ("madrid_pm.xml", madrid.get("intensity_sha256")),
        ("madrid_catalogue.csv", madrid.get("catalogue_sha256")),
        ("dft_aadf.json", dft.get("sha256")),
    ]
    checks: list[dict] = []
    for name, recorded in expected:
        path = _require(raw_dir / name)
        if not recorded:
            raise SystemExit(
                f"provenance.json records no SHA-256 for {name} — refusing to use unverifiable data. "
                "Re-run scripts/fetch_reference_counts.py."
            )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != str(recorded):
            raise SystemExit(
                f"SHA-256 mismatch for {path}: provenance records {recorded}, file hashes to {actual}. "
                "Raw data and provenance are out of sync — re-run scripts/fetch_reference_counts.py."
            )
        checks.append({"file": name, "sha256": actual, "matches_provenance": True})
    return checks


def gate_verdict(rule: str, plausibility: Mapping, envelope: Mapping, enough_cities: bool) -> str:
    """Dispatch the configured gate rule onto the matching evaluation's verdict."""
    if rule == "corridor_appropriate":
        primary = plausibility
    elif rule == "raw_percentile_spread":
        primary = envelope
    else:
        raise SystemExit(
            f"Unknown demand_reference_envelope.gate.rule {rule!r} — expected "
            "'corridor_appropriate' (evaluate_corridor_plausibility) or "
            "'raw_percentile_spread' (evaluate_demand_envelope)."
        )
    if primary["verdict"] == "plausible" and enough_cities:
        return "pass"
    return "review" if primary["verdict"] == "flagged" else "insufficient_reference"


def madrid_distribution(raw_dir: Path, only_urban: bool) -> dict:
    xml_text = _require(raw_dir / "madrid_pm.xml").read_text(encoding="utf-8", errors="replace")
    cat_text = _require(raw_dir / "madrid_catalogue.csv").read_text(encoding="utf-8", errors="replace")
    catalogue = rc.parse_madrid_catalogue(cat_text)
    values = rc.parse_madrid_intensities(xml_text, catalogue, only_urban=only_urban)
    dist = rc.distribution(values)
    dist["unit"] = "veh/h (measured, real-time snapshot)"
    return dist


def _local_authority_id(rec: dict) -> int:
    try:
        return int(rec.get("local_authority_id", -1))
    except (TypeError, ValueError):
        return -1


def dft_city_distributions(raw_dir: Path, provenance: dict, categories, k_factor: float) -> dict[str, dict]:
    records = json.loads(_require(raw_dir / "dft_aadf.json").read_text(encoding="utf-8"))
    id_to_city = {int(la_id): city for city, la_id in provenance_source(provenance, "dft")["local_authorities"].items()}
    out: dict[str, dict] = {}
    for la_id, city in sorted(id_to_city.items(), key=lambda item: item[1]):
        city_records = [rec for rec in records if _local_authority_id(rec) == la_id]
        # Filtragem A-roads + parsing numérico vivem na função da biblioteca
        # (unit-testada em tests/test_v2_reference_counts.py) — não duplicar aqui.
        aadfs = rc.parse_dft_aadf(city_records, road_categories=categories)
        vals = [rc.aadf_to_peak_hour_veh_h(aadf, k_factor) for aadf in aadfs]
        dist = rc.distribution(vals)
        dist["unit"] = "veh/h (AADF/dir × K peak-hour factor)"
        out[f"{city} (UK)"] = dist
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--v4d", type=Path, default=ROOT / "docs" / "validation" / "v4d_reference_corridor.json")
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "validation_config.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "v2_reference_demand_check.json")
    args = parser.parse_args()

    config = load_validation_config(args.config)
    env_cfg = config["demand_reference_envelope"]
    k_factor = float(env_cfg["dft_peak_hour_factor_k"]["value"])
    categories = env_cfg["dft_urban_road_categories"]["value"]
    only_urban = bool(env_cfg["madrid_only_urban"]["value"])
    percentiles = list(env_cfg["percentiles_checked"])
    min_cities = int(env_cfg["gate"]["min_reference_cities"])
    gate_rule = str(env_cfg["gate"]["rule"])

    provenance = json.loads(_require(args.raw_dir / "provenance.json").read_text(encoding="utf-8"))
    hash_checks = verify_raw_hashes(args.raw_dir, provenance)

    # Real reference distributions, one entry per European city.
    cities: dict[str, dict] = {}
    cities["Madrid (ES)"] = madrid_distribution(args.raw_dir, only_urban)
    cities.update(dft_city_distributions(args.raw_dir, provenance, categories, k_factor))

    # Modelled corridor intensity from the locally built V4d evidence.
    v4d_path = _require(
        args.v4d,
        hint="Build the V4d evidence with scripts/build_reference_corridor.py, or pass --v4d",
    ).resolve()
    v4d = json.loads(v4d_path.read_text(encoding="utf-8"))
    measured = v4d["demand"]["arterial_intensity_measured"]
    corridor_stats = {
        "median": measured.get("median_veh_h"),
        "p90": measured.get("p90_veh_h"),
        "mean": measured.get("mean_veh_h"),
        "max": measured.get("max_veh_h"),
    }

    # Two views: the naive same-percentile spread (transparency — shows the corridor
    # p90 sits below a whole city's heavy tail) and the corridor-appropriate gate
    # (the headline: typical-intensity match + not-implausibly-heavy).
    envelope = rc.evaluate_demand_envelope(corridor_stats, cities, percentiles=percentiles)
    plausibility = rc.evaluate_corridor_plausibility(corridor_stats, cities)
    enough_cities = len([c for c in cities.values() if c.get("n")]) >= min_cities
    verdict = gate_verdict(gate_rule, plausibility, envelope, enough_cities)

    report = {
        "validation_phase": "V2_reference_demand_envelope",
        "honest_framing": (
            "Reference-envelope TRANSFER, not a Porto calibration. Porto publishes no open traffic "
            "counts (CMP request declined), so the modelled Boavista arterial intensity is checked for "
            "plausibility against veh/h measured on real urban roads in other European cities. Every "
            "reference number traces to a fetched, hashed dataset; no Porto count is fabricated."
        ),
        "corridor_arterial_intensity_veh_h": corridor_stats,
        # Repo-relative when possible so the committed report carries no local home path.
        "corridor_source": (
            f"{v4d_path.relative_to(ROOT) if v4d_path.is_relative_to(ROOT) else v4d_path} "
            ":: demand.arterial_intensity_measured (built by scripts/build_reference_corridor.py)"
        ),
        "reference_cities": cities,
        "method": {
            "madrid": "informo real-time intensidad (veh/h), error=='N', URB detectors only (joined to catalogue).",
            "dft": f"AADF/dir for A-roads {categories} × K={k_factor} peak-hour factor.",
            "k_factor_source": env_cfg["dft_peak_hour_factor_k"]["source"],
            "gate_rule": gate_rule,
            "gate_source": env_cfg["gate"]["source"],
        },
        "corridor_plausibility": plausibility,
        "raw_percentile_spread_check": envelope,
        "provenance": provenance,
        "raw_file_hash_verification": hash_checks,
        "verdict": verdict,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"V2 reference demand envelope — {len(cities)} cities, percentiles {percentiles}")
    for name, dist in sorted(cities.items()):
        print(f"  {name:22s} n={dist.get('n', 0):4d}  median={dist.get('median')}  p90={dist.get('p90')}  veh/h")
    print(f"  corridor (Boavista)    median={corridor_stats['median']}  p90={corridor_stats['p90']}  veh/h")
    ti = plausibility.get("typical_intensity_match", {})
    we = plausibility.get("within_real_envelope", {})
    print(f"  [{'in' if ti.get('inside') else 'OUT'}] typical median {ti.get('corridor_median_veh_h')} "
          f"in real median range {ti.get('real_median_range_veh_h')}")
    print(f"  [{'in' if we.get('inside') else 'OUT'}] p90 {we.get('corridor_p90_veh_h')} "
          f"in real range {we.get('real_floor_to_peak_veh_h')}")
    print(f"  (raw same-percentile spread, for transparency:)")
    for chk in envelope["percentile_checks"]:
        print(f"     [{ 'in' if chk.get('inside') else 'OUT'}] {chk['percentile']}: corridor {chk.get('corridor_veh_h')} "
              f"vs band {chk.get('reference_band_veh_h')}")
    print(f"  verdict: {verdict}  -> {args.out}")
    if verdict not in ("pass",):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
