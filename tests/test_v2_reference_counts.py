#!/usr/bin/env python3
"""V2 reference-demand: parser and envelope-gate tests.

Every fixture below is a synthetic verification vector chosen so the expected
result is computable by hand (the mathematics / parsing rule is the source of
truth). NONE of these numbers is a traffic measurement and NONE asserts anything
about Porto: this suite proves the V2 instrument parses real payloads correctly
and applies the documented plausibility gate, nothing more. The real Madrid/DfT
numbers live only in the fetched payloads and the committed evidence report.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation import reference_counts as rc  # noqa: E402


MADRID_XML = (
    "﻿<pms>\n"
    "  <fecha_hora>10/06/2026 16:20:06</fecha_hora>\n"
    "  <pm><idelem>1</idelem><intensidad>600</intensidad><error>N</error></pm>\n"
    "  <pm><idelem>2</idelem><intensidad>900</intensidad><error>N</error></pm>\n"
    "  <pm><idelem>3</idelem><intensidad>5000</intensidad><error>N</error></pm>\n"
    "  <pm><idelem>4</idelem><intensidad>0</intensidad><error>S</error></pm>\n"
    "  <pm><idelem>5</idelem><intensidad>123</intensidad><error>N</error></pm>\n"
    "</pms>\n"
)
# Catalogue: detector 1,2 urban; 3 is M-30; 5 has no catalogue entry.
MADRID_CSV = (
    '"tipo_elem";"distrito";"id";"cod_cent";"nombre"\n'
    '"URB";4;1;"01001";"A"\n'
    '"URB";4;2;"01002";"B"\n'
    '"M30";4;3;"01003";"C"\n'
)

DFT_PAYLOAD = {
    "data": [
        {"local_authority_id": 3, "road_category": "PA", "year": 2024, "all_motor_vehicles": 1000},
        {"local_authority_id": 3, "road_category": "TA", "year": 2024, "all_motor_vehicles": 2000},
        {
            "local_authority_id": 3,
            "road_category": "TM",
            "year": 2024,
            "all_motor_vehicles": 9999,
        },  # motorway: excluded
        {
            "local_authority_id": 3,
            "road_category": "PA",
            "year": 2019,
            "all_motor_vehicles": 500,
        },  # wrong year
    ]
}


class MadridParsingTests(unittest.TestCase):
    def test_catalogue_maps_id_to_type(self) -> None:
        cat = rc.parse_madrid_catalogue(MADRID_CSV)
        self.assertEqual(cat, {1: "URB", 2: "URB", 3: "M30"})

    def test_error_flag_filters_invalid(self) -> None:
        # detector 4 has error=='S' -> dropped, regardless of urban filter.
        vals = rc.parse_madrid_intensities(MADRID_XML, None, only_urban=False)
        self.assertEqual(sorted(vals), [123.0, 600.0, 900.0, 5000.0])

    def test_urban_filter_drops_m30_and_uncatalogued(self) -> None:
        cat = rc.parse_madrid_catalogue(MADRID_CSV)
        vals = rc.parse_madrid_intensities(MADRID_XML, cat, only_urban=True)
        # keeps 1 (600) and 2 (900); drops 3 (M30), 4 (error), 5 (not in catalogue).
        self.assertEqual(sorted(vals), [600.0, 900.0])

    def test_feed_catalogue_coverage_counts_missing_detectors(self) -> None:
        # Valid (error=='N') detectors: 1, 2, 3, 5; the catalogue knows 1, 2, 3 →
        # detector 5 is the stale-snapshot case the fetch gate must surface.
        cov = rc.madrid_feed_catalogue_coverage(MADRID_XML, rc.parse_madrid_catalogue(MADRID_CSV))
        self.assertEqual(cov["feed_valid_detectors"], 4)
        self.assertEqual(cov["in_catalogue"], 3)
        self.assertEqual(cov["missing_from_catalogue"], 1)
        self.assertEqual(cov["coverage"], 0.75)

    def test_feed_catalogue_coverage_empty_feed(self) -> None:
        cov = rc.madrid_feed_catalogue_coverage("<pms></pms>", {1: "URB"})
        self.assertEqual(cov["feed_valid_detectors"], 0)
        self.assertIsNone(cov["coverage"])

    def test_catalogue_tolerates_utf8_bom(self) -> None:
        # The portal's sibling feed ships a BOM; a BOM'd catalogue header must not
        # silently parse to {} (which would drop every detector as non-URB).
        cat = rc.parse_madrid_catalogue("﻿" + MADRID_CSV)
        self.assertEqual(cat, {1: "URB", 2: "URB", 3: "M30"})


class DftParsingTests(unittest.TestCase):
    def test_filters_to_a_roads_and_year(self) -> None:
        vals = rc.parse_dft_aadf(DFT_PAYLOAD, road_categories=("PA", "TA"), year=2024)
        self.assertEqual(sorted(vals), [1000.0, 2000.0])

    def test_accepts_bare_record_list(self) -> None:
        # Regression: o formato que fetch_reference_counts.py grava em
        # dft_aadf.json é uma lista "bare" de registos (sem chave "data").
        vals = rc.parse_dft_aadf(DFT_PAYLOAD["data"], road_categories=("PA", "TA"))
        self.assertEqual(sorted(vals), [500.0, 1000.0, 2000.0])

    def test_accepts_list_of_pages(self) -> None:
        vals = rc.parse_dft_aadf([DFT_PAYLOAD], road_categories=("PA", "TA"), year=2024)
        self.assertEqual(sorted(vals), [1000.0, 2000.0])

    def test_aadf_to_peak_hour(self) -> None:
        self.assertAlmostEqual(rc.aadf_to_peak_hour_veh_h(1000.0, 0.10), 100.0)
        with self.assertRaises(ValueError):
            rc.aadf_to_peak_hour_veh_h(-1.0, 0.10)
        with self.assertRaises(ValueError):
            rc.aadf_to_peak_hour_veh_h(1000.0, 0.0)


class DistributionTests(unittest.TestCase):
    def test_percentile_matches_nearest_rank_convention(self) -> None:
        vals = list(range(1, 11))  # 1..10
        # int(10*0.5)=5 -> ordered[5]=6 ; int(10*0.9)=9 -> ordered[9]=10
        self.assertEqual(rc.percentile(vals, 0.50), 6)
        self.assertEqual(rc.percentile(vals, 0.90), 10)
        self.assertEqual(rc.percentile([42], 0.90), 42)

    def test_distribution_summary(self) -> None:
        d = rc.distribution([100, 200, 300, 400, 500])
        self.assertEqual(d["n"], 5)
        self.assertEqual(d["mean"], 300.0)
        self.assertEqual(d["median"], 300.0)  # int(5*0.5)=2 -> 300
        self.assertEqual(d["max"], 500.0)

    def test_empty_distribution(self) -> None:
        self.assertEqual(rc.distribution([]), {"n": 0})


class EnvelopeGateTests(unittest.TestCase):
    CITIES = {
        "A": {"n": 10, "median": 400.0, "p90": 1000.0},
        "B": {"n": 10, "median": 800.0, "p90": 2000.0},
    }

    def test_raw_same_percentile_flags_low_p90(self) -> None:
        # corridor p90 (700) below both cities' p90 band [1000, 2000] -> flagged.
        out = rc.evaluate_demand_envelope({"median": 600.0, "p90": 700.0}, self.CITIES)
        self.assertEqual(out["verdict"], "flagged")
        p90_check = next(c for c in out["percentile_checks"] if c["percentile"] == "p90")
        self.assertFalse(p90_check["inside"])

    def test_corridor_plausibility_accepts_low_p90(self) -> None:
        # median 600 in [400,800]; p90 700 in real range [400, 2000] -> plausible.
        out = rc.evaluate_corridor_plausibility({"median": 600.0, "p90": 700.0}, self.CITIES)
        self.assertEqual(out["verdict"], "plausible")
        self.assertTrue(out["typical_intensity_match"]["inside"])
        self.assertTrue(out["within_real_envelope"]["inside"])

    def test_corridor_plausibility_flags_atypical_median(self) -> None:
        # median 150 below the real median range [400,800] -> flagged.
        out = rc.evaluate_corridor_plausibility({"median": 150.0, "p90": 700.0}, self.CITIES)
        self.assertEqual(out["verdict"], "flagged")
        self.assertFalse(out["typical_intensity_match"]["inside"])

    def test_corridor_plausibility_flags_implausibly_heavy(self) -> None:
        # p90 5000 exceeds real peak 2000 -> flagged.
        out = rc.evaluate_corridor_plausibility({"median": 600.0, "p90": 5000.0}, self.CITIES)
        self.assertEqual(out["verdict"], "flagged")
        self.assertFalse(out["within_real_envelope"]["inside"])

    def test_no_reference_cities(self) -> None:
        out = rc.evaluate_corridor_plausibility({"median": 600.0, "p90": 700.0}, {})
        self.assertEqual(out["verdict"], "no_reference")


if __name__ == "__main__":
    unittest.main()
