#!/usr/bin/env python3
"""Inspect a local STCP GTFS zip and print candidate routes for PPS57.

Usage:
    python scripts/inspect_gtfs_routes.py --gtfs data/gtfs/gtfs_stcp_latest.zip --filter 500 502 205
"""
from __future__ import annotations

import argparse
import csv
import zipfile
from pathlib import Path


def read_gtfs_table(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with zf.open(name) as f:
        text = f.read().decode('utf-8-sig')
    return list(csv.DictReader(text.splitlines()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gtfs', required=True, type=Path)
    parser.add_argument('--filter', nargs='*', default=[])
    args = parser.parse_args()
    if not args.gtfs.exists():
        raise FileNotFoundError(args.gtfs)
    with zipfile.ZipFile(args.gtfs) as zf:
        routes = read_gtfs_table(zf, 'routes.txt')
        trips = read_gtfs_table(zf, 'trips.txt')
    filters = set(args.filter)
    route_trip_counts: dict[str, int] = {}
    for trip in trips:
        route_trip_counts[trip.get('route_id', '')] = route_trip_counts.get(trip.get('route_id', ''), 0) + 1
    for r in routes:
        short = r.get('route_short_name', '')
        long = r.get('route_long_name', '')
        rid = r.get('route_id', '')
        if filters and short not in filters and rid not in filters:
            continue
        print(f"route_id={rid} short={short} long={long} trips={route_trip_counts.get(rid, 0)}")


if __name__ == '__main__':
    main()
