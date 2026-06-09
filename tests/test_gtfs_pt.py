#!/usr/bin/env python3
"""V3 GTFS extractor tests.

The feed below is a tiny SYNTHETIC GTFS built in-memory to verify the extractor
logic (time parsing, weekday-service selection, headway computation, dwell
detection) against the GTFS spec and hand-computed values. It is a logic fixture,
NOT real STCP data, and asserts nothing about Porto.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation import gtfs_pt  # noqa: E402

_ROUTES = "route_id,route_short_name,route_long_name\nR1,999,Test Line\n"
_TRIPS = (
    "route_id,service_id,trip_id,direction_id\n"
    "R1,DIAS UTEIS,t1,0\n"
    "R1,DIAS UTEIS,t2,0\n"
    "R1,SABADOS,t3,0\n"  # weekend trip, must be excluded
)
_STOP_TIMES = (
    "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,07:00:00,07:00:00,S1,1\n"
    "t1,07:05:00,07:05:30,S2,2\n"  # 30 s dwell -> dwell encoded
    "t2,07:10:00,07:10:00,S1,1\n"
    "t2,07:15:00,07:15:00,S2,2\n"
    "t3,09:00:00,09:00:00,S1,1\n"  # excluded by service
)
_CALENDAR_DATES = (
    "service_id,date,exception_type\n"
    "DIAS UTEIS,20260101,1\n"
    "DIAS UTEIS,20260102,1\n"
    "SABADOS,20260103,1\n"
)


def _make_feed(directory: Path, stop_times: str = _STOP_TIMES) -> str:
    path = directory / "feed.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("routes.txt", _ROUTES)
        zf.writestr("trips.txt", _TRIPS)
        zf.writestr("stop_times.txt", stop_times)
        zf.writestr("calendar_dates.txt", _CALENDAR_DATES)
    return str(path)


class TimeTests(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(gtfs_pt.gtfs_time_to_seconds("07:05:30"), 25530)

    def test_after_midnight(self) -> None:
        self.assertEqual(gtfs_pt.gtfs_time_to_seconds("25:30:00"), 91800)


class HeadwayStatTests(unittest.TestCase):
    def test_even_headways(self) -> None:
        stats = gtfs_pt.headway_stats([0, 600, 1200], (0, 3600))
        self.assertEqual(stats["n_trips"], 3)
        self.assertAlmostEqual(stats["mean_headway_min"], 10.0)
        self.assertAlmostEqual(stats["median_headway_min"], 10.0)

    def test_single_trip(self) -> None:
        self.assertEqual(gtfs_pt.headway_stats([100], (0, 3600)), {"n_trips": 1})

    def test_empty_window(self) -> None:
        self.assertIsNone(gtfs_pt.headway_stats([], (0, 3600)))


class ServiceSelectionTests(unittest.TestCase):
    def test_preferred_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed = _make_feed(Path(tmp))
            self.assertEqual(gtfs_pt.select_weekday_service_id(feed, "DIAS UTEIS"), "DIAS UTEIS")

    def test_preferred_absent_falls_back_to_most_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed = _make_feed(Path(tmp))
            # DIAS UTEIS has 2 dates vs SABADOS 1 -> chosen as the weekday proxy.
            self.assertEqual(gtfs_pt.select_weekday_service_id(feed, "NONEXISTENT"), "DIAS UTEIS")

    def test_calendar_txt_weekday_service(self) -> None:
        # Standard GTFS: regular weekly service in calendar.txt (no calendar_dates.txt).
        calendar = (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "WEEKDAY,1,1,1,1,1,0,0,20260101,20261231\n"
            "WEEKEND,0,0,0,0,0,1,1,20260101,20261231\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("routes.txt", _ROUTES)
                zf.writestr("trips.txt", _TRIPS)
                zf.writestr("stop_times.txt", _STOP_TIMES)
                zf.writestr("calendar.txt", calendar)
            self.assertEqual(gtfs_pt.select_weekday_service_id(str(path), "NONEXISTENT"), "WEEKDAY")


class ExtractTests(unittest.TestCase):
    def test_headways_and_dwell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed = _make_feed(Path(tmp))
            result = gtfs_pt.extract_corridor_headways(
                feed, ["999"], windows={"am_peak": (7 * 3600, 9 * 3600)}
            )
        self.assertEqual(result["service_id"], "DIAS UTEIS")
        self.assertTrue(result["dwell_encoded_in_gtfs"])  # t1 has a 30 s dwell
        line = result["lines"]["999"]
        self.assertTrue(line["present_in_feed"])
        direction = line["directions"]["0"]
        self.assertEqual(direction["weekday_trips"], 2)  # t3 (weekend) excluded
        # first departures 07:00 and 07:10 -> a single 10-minute headway
        self.assertAlmostEqual(direction["windows"]["am_peak"]["median_headway_min"], 10.0)

    def test_first_departure_uses_min_sequence(self) -> None:
        # GTFS only requires stop_sequence to increase; here the first stop is numbered 5.
        stop_times = (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "t1,07:00:00,07:00:00,S1,5\n"
            "t1,07:05:00,07:05:00,S2,6\n"
            "t2,07:12:00,07:12:00,S1,5\n"
            "t2,07:17:00,07:17:00,S2,6\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            feed = _make_feed(Path(tmp), stop_times)
            result = gtfs_pt.extract_corridor_headways(feed, ["999"], windows={"am_peak": (7 * 3600, 9 * 3600)})
        direction = result["lines"]["999"]["directions"]["0"]
        self.assertEqual(direction["weekday_trips"], 2)  # captured despite sequence starting at 5
        self.assertAlmostEqual(direction["windows"]["am_peak"]["median_headway_min"], 12.0)

    def test_absent_line_marked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed = _make_feed(Path(tmp))
            result = gtfs_pt.extract_corridor_headways(feed, ["404"])
        self.assertFalse(result["lines"]["404"]["present_in_feed"])


if __name__ == "__main__":
    unittest.main()
