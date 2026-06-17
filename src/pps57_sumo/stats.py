#!/usr/bin/env python3
"""Shared sample statistics (Student-t 95% CI).

Extracted from run_sumo_scenario.py so the same Student-t confidence-interval
machinery is reused by the scenario replication aggregation and by the
off-policy-evaluation report (pps57_opt.ope) — one implementation, one t-table.
"""

from __future__ import annotations

import math
import statistics

T_CRITICAL_95: dict[int, float] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def t_critical_95(df: int) -> float:
    if df <= 0:
        return 0.0
    if df in T_CRITICAL_95:
        return T_CRITICAL_95[df]
    return 1.96  # df > 30: aproximação normal


def mean_ci95(values: list[float]) -> dict[str, float | int | None]:
    """Média e intervalo de confiança 95% (t de Student, stdev amostral)."""
    n = len(values)
    if n == 0:
        return {
            "mean": None,
            "n": 0,
            "stdev_sample": None,
            "sem": None,
            "ci95_half_width": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    mean = statistics.fmean(values)
    if n == 1:
        return {
            "mean": round(mean, 3),
            "n": 1,
            "stdev_sample": 0.0,
            "sem": 0.0,
            "ci95_half_width": 0.0,
            "ci95_low": round(mean, 3),
            "ci95_high": round(mean, 3),
        }
    stdev = statistics.stdev(values)  # amostral (n-1), apropriado para inferência
    sem = stdev / math.sqrt(n)
    half = t_critical_95(n - 1) * sem
    return {
        "mean": round(mean, 3),
        "n": n,
        "stdev_sample": round(stdev, 3),
        "sem": round(sem, 3),
        "ci95_half_width": round(half, 3),
        "ci95_low": round(mean - half, 3),
        "ci95_high": round(mean + half, 3),
    }
