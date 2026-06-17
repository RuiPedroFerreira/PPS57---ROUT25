#!/usr/bin/env python3
"""Extract real public-transport schedule facts from a GTFS feed (V3).

Pure-stdlib reader for the parts of GTFS that anchor the simulation's public
transport: real **headways** (trip frequency by clock window and direction) and
whether **dwell** is encoded. It consumes a real GTFS zip (the source of truth,
e.g. the CC0 STCP feed) and invents nothing.

GTFS reference: https://gtfs.org/schedule/reference/ . Headways are derived from
the dispatch times (departure at the first stop) of the weekday trips of a route;
dwell is `departure_time - arrival_time` per stop_time (often 0/unencoded).
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from statistics import fmean, median

# Clock windows (seconds since midnight) used to report headways.
DEFAULT_WINDOWS: dict[str, tuple[int, int]] = {
    "am_peak": (7 * 3600, 9 * 3600),
    "midday": (11 * 3600, 14 * 3600),
    "pm_peak": (17 * 3600, 19 * 3600),
}


def gtfs_time_to_seconds(value: str) -> int:
    """Parse a GTFS HH:MM:SS time to seconds since midnight (hours may exceed 24)."""
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Malformed GTFS time string {value!r}") from exc
    return hours * 3600 + minutes * 60 + seconds


def _read_table(zip_file: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with zip_file.open(name) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig"))


def select_weekday_service_id(zip_path: str, preferred: str = "DIAS UTEIS") -> str:
    """Pick the weekday service_id, supporting both GTFS service-definition styles.

    Standard GTFS defines regular weekly service in ``calendar.txt`` (monday..sunday
    flags) and uses ``calendar_dates.txt`` only for exceptions; some feeds (e.g. STCP)
    use ``calendar_dates.txt`` for the regular schedule. Prefer the named service; else
    the one with the most weekday coverage (calendar.txt) or the most active dates
    (calendar_dates.txt).
    """
    with zipfile.ZipFile(zip_path) as zip_file:
        names = set(zip_file.namelist())
        if "calendar.txt" in names:
            weekday: dict[str, int] = {}
            for row in _read_table(zip_file, "calendar.txt"):
                served = sum(
                    int(row.get(day, "0") or "0")
                    for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
                )
                if served > 0:
                    weekday[row["service_id"]] = served
            if preferred in weekday:
                return preferred
            if weekday:
                return max(weekday, key=weekday.get)
        if "calendar_dates.txt" in names:
            counts: dict[str, int] = {}
            for row in _read_table(zip_file, "calendar_dates.txt"):
                # exception_type=1 adds service on that date; 2 REMOVES it.
                # Counting removals would inflate exception-heavy services.
                if (row.get("exception_type") or "").strip() != "1":
                    continue
                counts[row["service_id"]] = counts.get(row["service_id"], 0) + 1
            if preferred in counts:
                return preferred
            if counts:
                return max(counts, key=counts.get)
    raise ValueError("feed has no calendar.txt or calendar_dates.txt weekday service")


def headway_stats(departures_s: Sequence[int], window: tuple[int, int]) -> dict[str, float] | None:
    """Headway statistics (minutes) for departures falling inside a clock window."""
    start, end = window
    inside = sorted(d for d in departures_s if start <= d < end)
    if len(inside) < 2:
        return {"n_trips": len(inside)} if inside else None
    gaps_min = [(inside[i] - inside[i - 1]) / 60.0 for i in range(1, len(inside))]
    return {
        "n_trips": len(inside),
        "mean_headway_min": round(fmean(gaps_min), 2),
        "median_headway_min": round(median(gaps_min), 2),
        "min_headway_min": round(min(gaps_min), 2),
        "max_headway_min": round(max(gaps_min), 2),
    }


def extract_corridor_headways(
    zip_path: str,
    short_names: Iterable[str],
    *,
    windows: Mapping[str, tuple[int, int]] | None = None,
    preferred_service: str = "DIAS UTEIS",
) -> dict[str, object]:
    """Extract real weekday headways (by direction and clock window) for the given
    route short names, plus whether the feed encodes dwell.

    Streams stop_times.txt filtered to the target trips, so it stays light on
    large feeds. Returns a JSON-serialisable structure; fabricates nothing.
    """
    windows = dict(windows or DEFAULT_WINDOWS)
    short_set = {str(name) for name in short_names}
    service_id = select_weekday_service_id(zip_path, preferred_service)

    with zipfile.ZipFile(zip_path) as zip_file:
        rid_to_short: dict[str, str] = {}
        long_names: dict[str, str] = {}
        for row in _read_table(zip_file, "routes.txt"):
            if row.get("route_short_name") in short_set:
                rid_to_short[row["route_id"]] = row["route_short_name"]
                long_names[row["route_short_name"]] = row.get("route_long_name", "")

        trip_key: dict[str, tuple[str, str]] = {}
        for row in _read_table(zip_file, "trips.txt"):
            if row.get("route_id") in rid_to_short and row.get("service_id") == service_id:
                trip_key[row["trip_id"]] = (
                    rid_to_short[row["route_id"]],
                    row.get("direction_id", ""),
                )

        # Trip's first departure = the departure at its MINIMUM stop_sequence. GTFS only
        # requires stop_sequence to increase along the trip, not to start at "1"; keying
        # off the literal "1" would drop every departure for feeds numbered otherwise.
        trip_first: dict[str, tuple[int, int]] = {}  # trip_id -> (min_seq, departure_s)
        target_rows = 0
        dwell_nonzero = 0
        for row in _read_table(zip_file, "stop_times.txt"):
            tid = row["trip_id"]
            if tid not in trip_key:
                continue
            target_rows += 1
            arrival = (row.get("arrival_time") or "").strip()
            departure = (row.get("departure_time") or "").strip()
            if not arrival or not departure:
                # GTFS allows blank times on non-timepoint stops; skip the row
                # instead of fabricating (or crashing on) a missing time.
                continue
            if arrival != departure:
                dwell_nonzero += 1
            seq = int(row["stop_sequence"])
            if tid not in trip_first or seq < trip_first[tid][0]:
                try:
                    dep_s = gtfs_time_to_seconds(departure)
                except ValueError:
                    continue
                trip_first[tid] = (seq, dep_s)
        first_departures: dict[tuple[str, str], list[int]] = {}
        for tid, (_seq, dep_s) in trip_first.items():
            first_departures.setdefault(trip_key[tid], []).append(dep_s)

    lines: dict[str, dict[str, object]] = {}
    for short in sorted(short_set):
        directions: dict[str, object] = {}
        for (line_short, direction), departures in first_departures.items():
            if line_short != short:
                continue
            departures = sorted(departures)
            directions[direction or "?"] = {
                "weekday_trips": len(departures),
                "first_dep_span_h": [departures[0] // 3600, departures[-1] // 3600]
                if departures
                else [],
                "windows": {name: headway_stats(departures, win) for name, win in windows.items()},
            }
        lines[short] = {
            "route_long_name": long_names.get(short, ""),
            "present_in_feed": short in long_names,
            "directions": directions,
        }

    return {
        "service_id": service_id,
        "dwell_encoded_in_gtfs": dwell_nonzero > 0,
        "target_stop_time_rows": target_rows,
        "windows_clock": {name: [win[0], win[1]] for name, win in windows.items()},
        "lines": lines,
    }
