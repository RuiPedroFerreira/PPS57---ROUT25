#!/usr/bin/env python3
"""Comparison tables for baseline vs RL TSP runtime runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List
import json


ComparisonRow = Dict[str, object]


def build_tsp_ab_comparison_rows(
    baseline_summary: Dict[str, Any],
    rl_summary: Dict[str, Any],
) -> List[ComparisonRow]:
    rows: List[ComparisonRow] = []

    for key in [
        "total_decisions",
        "cits_acknowledged_messages",
        "actuation_events",
        "applied_events",
        "blocked_by_safety",
    ]:
        rows.append(_row(key, baseline_summary.get(key, 0), rl_summary.get(key, 0)))

    for action in _sorted_union_keys(
        baseline_summary.get("by_action"), rl_summary.get("by_action")
    ):
        rows.append(
            _row(
                f"action:{action}",
                baseline_summary.get("by_action", {}).get(action, 0),
                rl_summary.get("by_action", {}).get(action, 0),
            )
        )

    for status in _sorted_union_keys(
        baseline_summary.get("by_status"), rl_summary.get("by_status")
    ):
        rows.append(
            _row(
                f"status:{status}",
                baseline_summary.get("by_status", {}).get(status, 0),
                rl_summary.get("by_status", {}).get(status, 0),
            )
        )

    rl_policy = rl_summary.get("runtime_policy", {})
    rows.extend(
        [
            _row(
                "runtime_policy_loaded",
                baseline_summary.get("runtime_policy_loaded"),
                rl_summary.get("runtime_policy_loaded"),
            ),
            _metadata_row("rl_policy_id", rl_policy.get("policy_id")),
            _metadata_row("rl_algorithm", rl_policy.get("algorithm")),
            _metadata_row("rl_rule_count", rl_policy.get("rule_count")),
            _metadata_row("rl_policy_source", rl_policy.get("source_path")),
        ]
    )
    return rows


def write_tsp_ab_comparison(
    baseline_summary: Dict[str, Any],
    rl_summary: Dict[str, Any],
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> Dict[str, object]:
    rows = build_tsp_ab_comparison_rows(baseline_summary, rl_summary)
    payload = {
        "comparison": "baseline_vs_rl_tsp_runtime",
        "baseline_mode": baseline_summary.get("policy_mode", "baseline"),
        "rl_mode": rl_summary.get("policy_mode", "rl"),
        "rows": rows,
    }

    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    markdown_output.write_text(render_markdown_table(rows), encoding="utf-8")
    return payload


def build_kpi_comparison_rows(
    baseline_kpis: Dict[str, Any],
    rl_kpis: Dict[str, Any],
) -> List[ComparisonRow]:
    rows: List[ComparisonRow] = []
    for group in [
        "all_vehicles",
        "priority_vehicles",
        "buses",
        "emergency_vehicles",
        "general_traffic",
        "non_priority_vehicles",
    ]:
        baseline_group = baseline_kpis.get(group, {})
        rl_group = rl_kpis.get(group, {})
        for metric in [
            "vehicles",
            "mean_duration_s",
            "mean_waiting_time_s",
            "mean_time_loss_s",
            "p95_time_loss_s",
            "mean_depart_delay_s",
            "mean_stop_count",
        ]:
            rows.append(_row(f"{group}:{metric}", baseline_group.get(metric), rl_group.get(metric)))
    return rows


def write_kpi_comparison(
    baseline_kpis: Dict[str, Any],
    rl_kpis: Dict[str, Any],
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> Dict[str, object]:
    rows = build_kpi_comparison_rows(baseline_kpis, rl_kpis)
    payload = {
        "comparison": "baseline_vs_rl_sumo_kpis",
        "baseline_source": baseline_kpis.get("source", ""),
        "rl_source": rl_kpis.get("source", ""),
        "rows": rows,
    }
    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    markdown_output.write_text(
        render_markdown_table(rows, title="SUMO KPI Baseline vs RL Comparison"), encoding="utf-8"
    )
    return payload


def render_markdown_table(
    rows: Iterable[ComparisonRow], *, title: str = "TSP Baseline vs RL Comparison"
) -> str:
    lines = [
        f"# {title}",
        "",
        "| Metric | Baseline | RL | Delta RL-Baseline |",
        "|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {metric} | {baseline} | {rl} | {delta} |".format(
                metric=_escape_markdown(str(row.get("metric", ""))),
                baseline=_escape_markdown(_format_value(row.get("baseline"))),
                rl=_escape_markdown(_format_value(row.get("rl"))),
                delta=_escape_markdown(_format_value(row.get("delta"))),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _row(metric: str, baseline: Any, rl: Any) -> ComparisonRow:
    return {
        "metric": metric,
        "baseline": baseline,
        "rl": rl,
        "delta": _delta(baseline, rl),
    }


def _metadata_row(metric: str, rl: Any) -> ComparisonRow:
    return {
        "metric": metric,
        "baseline": "",
        "rl": rl if rl is not None else "",
        "delta": "",
    }


def _delta(baseline: Any, rl: Any) -> int | float | str:
    if isinstance(baseline, bool) or isinstance(rl, bool):
        return ""
    if isinstance(baseline, (int, float)) and isinstance(rl, (int, float)):
        return round(rl - baseline, 4)
    return ""


def _sorted_union_keys(left: Any, right: Any) -> List[str]:
    left_keys = set(left.keys()) if isinstance(left, dict) else set()
    right_keys = set(right.keys()) if isinstance(right, dict) else set()
    return sorted(str(item) for item in left_keys | right_keys)


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")
