#!/usr/bin/env python3
"""Conservative outcome evaluation for baseline vs RL TSP decisions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pps57_tsp.models import DecisionStatus, TSPAction

Row = dict[str, object]


def evaluate_decision_outcomes(
    *,
    baseline_summary: dict[str, Any],
    rl_summary: dict[str, Any],
    baseline_decisions: Iterable[dict[str, Any]],
    baseline_actuations: Iterable[dict[str, Any]],
    rl_decisions: Iterable[dict[str, Any]],
    rl_actuations: Iterable[dict[str, Any]],
    baseline_kpis: dict[str, Any] | None = None,
    rl_kpis: dict[str, Any] | None = None,
) -> dict[str, object]:
    baseline_decision_list = list(baseline_decisions)
    rl_decision_list = list(rl_decisions)
    # Cada chave guarda a LISTA de decisões: múltiplas decisões para o mesmo
    # (timestamp, veículo, TLS) são emparelhadas pelo request_id estável (ver
    # _pair_within_key) em vez de por ordem de log, evitando comparar decisões
    # logicamente diferentes quando as duas corridas registam a colisão por
    # ordens distintas. Não são colapsadas silenciosamente em last-wins.
    baseline_by_key = _decisions_by_key(baseline_decision_list)
    rl_by_key = _decisions_by_key(rl_decision_list)
    baseline_actuation_by_decision = _actuations_by_decision_id(baseline_actuations)
    rl_actuation_by_decision = _actuations_by_decision_id(rl_actuations)

    rows: list[Row] = []
    missing_baseline = 0
    missing_rl = 0
    for key in sorted(set(baseline_by_key) | set(rl_by_key)):
        baseline_items = baseline_by_key.get(key, [])
        rl_items = rl_by_key.get(key, [])
        paired, baseline_unpaired, rl_unpaired = _pair_within_key(baseline_items, rl_items)
        for baseline, rl in paired:
            rows.append(
                _evaluate_pair(
                    key,
                    baseline,
                    baseline_actuation_by_decision.get(str(baseline.get("decision_id")), {}),
                    rl,
                    rl_actuation_by_decision.get(str(rl.get("decision_id")), {}),
                )
            )
        for rl in rl_unpaired:
            missing_baseline += 1
            rows.append(_missing_row(key, "missing_baseline", rl))
        for baseline in baseline_unpaired:
            missing_rl += 1
            rows.append(_missing_row(key, "missing_rl", baseline))

    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict = str(row.get("verdict", "unknown"))
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    kpi_evaluation = _evaluate_kpis(baseline_kpis, rl_kpis)
    return {
        "evaluation": "baseline_vs_rl_decision_outcomes",
        "decision_count": len(rows),
        "matched_decision_count": len(rows) - missing_baseline - missing_rl,
        "missing_baseline_count": missing_baseline,
        "missing_rl_count": missing_rl,
        "pairing_key_collisions": {
            "baseline": _collision_count(baseline_by_key),
            "rl": _collision_count(rl_by_key),
        },
        "verdict_counts": verdict_counts,
        "baseline_summary": _summary_projection(baseline_summary),
        "rl_summary": _summary_projection(rl_summary),
        "kpi_evaluation": kpi_evaluation,
        "network_impact_verdict": _network_impact_verdict(kpi_evaluation),
        "rows": rows,
    }


def write_decision_outcome_evaluation(
    *,
    baseline_summary: dict[str, Any],
    rl_summary: dict[str, Any],
    baseline_decision_log: str | Path,
    baseline_actuation_log: str | Path,
    rl_decision_log: str | Path,
    rl_actuation_log: str | Path,
    json_path: str | Path,
    markdown_path: str | Path,
    baseline_kpis: dict[str, Any] | None = None,
    rl_kpis: dict[str, Any] | None = None,
) -> dict[str, object]:
    payload = evaluate_decision_outcomes(
        baseline_summary=baseline_summary,
        rl_summary=rl_summary,
        baseline_decisions=_read_jsonl(Path(baseline_decision_log)),
        baseline_actuations=_read_jsonl(Path(baseline_actuation_log)),
        rl_decisions=_read_jsonl(Path(rl_decision_log)),
        rl_actuations=_read_jsonl(Path(rl_actuation_log)),
        baseline_kpis=baseline_kpis,
        rl_kpis=rl_kpis,
    )
    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    markdown_output.write_text(render_outcome_markdown(payload), encoding="utf-8")
    return payload


def render_outcome_markdown(payload: dict[str, object]) -> str:
    rows = list(payload.get("rows", []))
    lines = [
        "# Decision Outcome Evaluation",
        "",
        f"- matched_decision_count: {payload.get('matched_decision_count', 0)}",
        f"- network_impact_verdict: {payload.get('network_impact_verdict', 'unknown')}",
        f"- verdict_counts: {json.dumps(payload.get('verdict_counts', {}), ensure_ascii=False, sort_keys=True)}",
        "",
        "| Time | Vehicle | TLS | Baseline | RL | Safety Delta | Actuation Delta | Verdict |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {time} | {vehicle} | {tls} | {baseline} | {rl} | {safety_delta} | {actuation_delta} | {verdict} |".format(
                time=_fmt(row.get("timestamp_s")),
                vehicle=_escape(str(row.get("vehicle_id", ""))),
                tls=_escape(str(row.get("tls_id", ""))),
                baseline=_escape(
                    f"{row.get('baseline_action', '')}/{row.get('baseline_status', '')}"
                ),
                rl=_escape(f"{row.get('rl_action', '')}/{row.get('rl_status', '')}"),
                safety_delta=_escape(str(row.get("safety_delta", ""))),
                actuation_delta=_escape(str(row.get("actuation_delta", ""))),
                verdict=_escape(str(row.get("verdict", ""))),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _evaluate_pair(
    key: tuple[float, str, str],
    baseline: dict[str, Any],
    baseline_actuation: dict[str, Any],
    rl: dict[str, Any],
    rl_actuation: dict[str, Any],
) -> Row:
    baseline_status = str(baseline.get("status", ""))
    rl_status = str(rl.get("status", ""))
    baseline_action = str(baseline.get("action", ""))
    rl_action = str(rl.get("action", ""))
    baseline_applied = bool(baseline_actuation.get("applied", False))
    rl_applied = bool(rl_actuation.get("applied", False))
    safety_delta = _safety_delta(baseline_status, rl_status)
    actuation_delta = _actuation_delta(baseline_applied, rl_applied)
    verdict, reason = _verdict(
        baseline_action=baseline_action,
        baseline_status=baseline_status,
        baseline_applied=baseline_applied,
        rl_action=rl_action,
        rl_status=rl_status,
        rl_applied=rl_applied,
        safety_delta=safety_delta,
        actuation_delta=actuation_delta,
    )
    return {
        "key": "|".join([_fmt(key[0]), key[1], key[2]]),
        "timestamp_s": key[0],
        "vehicle_id": key[1],
        "tls_id": key[2],
        "baseline_decision_id": baseline.get("decision_id", ""),
        "rl_decision_id": rl.get("decision_id", ""),
        "baseline_action": baseline_action,
        "rl_action": rl_action,
        "baseline_status": baseline_status,
        "rl_status": rl_status,
        "baseline_reason": baseline.get("reason", ""),
        "rl_reason": rl.get("reason", ""),
        "baseline_applied": baseline_applied,
        "rl_applied": rl_applied,
        "action_changed": baseline_action != rl_action,
        "safety_delta": safety_delta,
        "actuation_delta": actuation_delta,
        "bus_delay_delta_s": None,
        "general_traffic_delay_delta_s": None,
        "collision_or_conflict": None,
        "verdict": verdict,
        "verdict_reason": reason,
    }


def _verdict(
    *,
    baseline_action: str,
    baseline_status: str,
    baseline_applied: bool,
    rl_action: str,
    rl_status: str,
    rl_applied: bool,
    safety_delta: str,
    actuation_delta: str,
) -> tuple[str, str]:
    if (
        rl_status == DecisionStatus.BLOCKED_BY_SAFETY.value
        and baseline_status != DecisionStatus.BLOCKED_BY_SAFETY.value
    ):
        return (
            "unsafe_or_blocked",
            "RL proposal was blocked by the Safety Layer while baseline was not.",
        )
    if safety_delta == "less_blocked":
        return "safer_or_less_intrusive", "RL avoided a Safety Layer block observed in baseline."
    if (
        baseline_action == rl_action
        and baseline_status == rl_status
        and baseline_applied == rl_applied
    ):
        return "same", "RL produced the same observable decision outcome as baseline."
    if (
        _requires_actuation(baseline_action)
        and not _requires_actuation(rl_action)
        and rl_status != DecisionStatus.BLOCKED_BY_SAFETY.value
    ):
        return (
            "safer_or_less_intrusive",
            "RL chose a non-actuating alternative to a baseline actuating action; network impact still needs KPI evidence.",
        )
    if rl_applied and not baseline_applied:
        return (
            "inconclusive",
            "RL added actuation; KPI evidence is required before calling this better.",
        )
    if actuation_delta == "less_actuation":
        return (
            "safer_or_less_intrusive",
            "RL reduced actuation; KPI evidence is still required for network benefit.",
        )
    return (
        "inconclusive",
        "Decision changed, but available logs do not prove network benefit or harm.",
    )


def _evaluate_kpis(
    baseline_kpis: dict[str, Any] | None, rl_kpis: dict[str, Any] | None
) -> dict[str, object]:
    if not baseline_kpis or not rl_kpis:
        return {
            "available": False,
            "reason": "No paired SUMO KPI reports were provided; network delay and collision impact remain inconclusive.",
            "rows": [],
        }
    rows: list[Row] = []
    for group in ["all_vehicles", "buses", "general_traffic"]:
        base_group = baseline_kpis.get(group, {})
        rl_group = rl_kpis.get(group, {})
        for metric in ["mean_duration_s", "mean_waiting_time_s", "mean_time_loss_s"]:
            rows.append(
                {
                    "metric": f"{group}:{metric}",
                    "baseline": base_group.get(metric),
                    "rl": rl_group.get(metric),
                    "delta": _numeric_delta(base_group.get(metric), rl_group.get(metric)),
                }
            )
    return {"available": True, "rows": rows}


def _network_impact_verdict(kpi_evaluation: dict[str, object]) -> str:
    if not kpi_evaluation.get("available"):
        return "inconclusive_without_kpis"
    rows = list(kpi_evaluation.get("rows", []))
    bus_loss = _row_delta(rows, "buses:mean_time_loss_s")
    traffic_loss = _row_delta(rows, "general_traffic:mean_time_loss_s")
    if bus_loss is not None and traffic_loss is not None:
        if bus_loss < 0 and traffic_loss <= 0:
            return "better_with_available_kpis"
        if bus_loss < 0 and traffic_loss <= abs(bus_loss):
            return "tradeoff_potentially_acceptable"
        if bus_loss > 0 or traffic_loss > 0:
            return "worse_or_costly_with_available_kpis"
    return "inconclusive_with_available_kpis"


def _decision_key(item: dict[str, Any]) -> tuple[float, str, str]:
    return (
        round(float(item.get("timestamp_s", 0.0)), 3),
        str(item.get("vehicle_id", "")),
        str(item.get("tls_id", "")),
    )


def _decisions_by_key(
    items: Iterable[dict[str, Any]],
) -> dict[tuple[float, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[float, str, str], list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(_decision_key(item), []).append(item)
    return grouped


def _collision_count(grouped: dict[tuple[float, str, str], list[dict[str, Any]]]) -> int:
    return sum(len(items) - 1 for items in grouped.values() if len(items) > 1)


def _stable_subkey(item: dict[str, Any]) -> str | None:
    """Identificador estável entre corridas para alinhar decisões dentro de uma
    chave colidida. Usa-se `request_id` (correlation_token determinístico). O
    `decision_id` é um uuid4 gerado por decisão — único por corrida e portanto
    inútil para emparelhar baseline↔RL — pelo que NÃO é usado aqui."""
    raw = item.get("request_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _pair_within_key(
    baseline_items: list[dict[str, Any]],
    rl_items: list[dict[str, Any]],
) -> tuple[
    list[tuple[dict[str, Any], dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Empareja as decisões de uma chave (timestamp, veículo, TLS) pelo request_id
    estável, devolvendo (pares, baseline_sem_par, rl_sem_par).

    Decisões sem request_id caem para emparelhamento posicional (comportamento
    legado), mas apenas entre si — nunca se mistura uma decisão com request_id
    conhecido com outra sem ele. Quando duas decisões partilham timestamp, veículo,
    TLS *e* request_id (colisão dupla, rara) o posicional dentro desse sub-grupo é
    o melhor disponível, pois trata-se do mesmo pedido lógico."""
    baseline_keyed: dict[str, list[dict[str, Any]]] = {}
    baseline_unkeyed: list[dict[str, Any]] = []
    for item in baseline_items:
        sub = _stable_subkey(item)
        if sub is None:
            baseline_unkeyed.append(item)
        else:
            baseline_keyed.setdefault(sub, []).append(item)

    rl_keyed: dict[str, list[dict[str, Any]]] = {}
    rl_unkeyed: list[dict[str, Any]] = []
    for item in rl_items:
        sub = _stable_subkey(item)
        if sub is None:
            rl_unkeyed.append(item)
        else:
            rl_keyed.setdefault(sub, []).append(item)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    baseline_unpaired: list[dict[str, Any]] = []
    rl_unpaired: list[dict[str, Any]] = []

    # Ordem determinística: sub-keys pela ordem de aparição no baseline, depois
    # as que só existem no RL.
    ordered_subs = list(baseline_keyed.keys())
    ordered_subs.extend(sub for sub in rl_keyed if sub not in baseline_keyed)
    for sub in ordered_subs:
        b_list = baseline_keyed.get(sub, [])
        r_list = rl_keyed.get(sub, [])
        for baseline, rl in zip(b_list, r_list, strict=False):
            pairs.append((baseline, rl))
        baseline_unpaired.extend(b_list[len(r_list) :])
        rl_unpaired.extend(r_list[len(b_list) :])

    # Fallback posicional para decisões sem identificador estável.
    for baseline, rl in zip(baseline_unkeyed, rl_unkeyed, strict=False):
        pairs.append((baseline, rl))
    baseline_unpaired.extend(baseline_unkeyed[len(rl_unkeyed) :])
    rl_unpaired.extend(rl_unkeyed[len(baseline_unkeyed) :])

    return pairs, baseline_unpaired, rl_unpaired


def _actuations_by_decision_id(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("decision_id")): row for row in rows if row.get("decision_id")}


def _missing_row(key: tuple[float, str, str], verdict: str, item: dict[str, Any] | None) -> Row:
    return {
        "key": "|".join([_fmt(key[0]), key[1], key[2]]),
        "timestamp_s": key[0],
        "vehicle_id": key[1],
        "tls_id": key[2],
        "baseline_action": item.get("action", "") if verdict == "missing_rl" and item else "",
        "rl_action": item.get("action", "") if verdict == "missing_baseline" and item else "",
        "baseline_status": item.get("status", "") if verdict == "missing_rl" and item else "",
        "rl_status": item.get("status", "") if verdict == "missing_baseline" and item else "",
        "safety_delta": "unmatched",
        "actuation_delta": "unmatched",
        "verdict": verdict,
        "verdict_reason": "Decision exists in only one run; paired comparison is not possible.",
    }


def _safety_delta(baseline_status: str, rl_status: str) -> str:
    blocked = DecisionStatus.BLOCKED_BY_SAFETY.value
    if baseline_status == blocked and rl_status != blocked:
        return "less_blocked"
    if baseline_status != blocked and rl_status == blocked:
        return "more_blocked"
    return "same_blocking_class"


def _actuation_delta(baseline_applied: bool, rl_applied: bool) -> str:
    if baseline_applied and not rl_applied:
        return "less_actuation"
    if not baseline_applied and rl_applied:
        return "more_actuation"
    return "same_actuation"


def _summary_projection(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_mode": summary.get("policy_mode"),
        "total_decisions": summary.get("total_decisions"),
        "by_action": summary.get("by_action", {}),
        "by_status": summary.get("by_status", {}),
        "applied_events": summary.get("applied_events"),
        "blocked_by_safety": summary.get("blocked_by_safety"),
        "runtime_policy_loaded": summary.get("runtime_policy_loaded"),
    }


def _requires_actuation(action: str) -> bool:
    return action in {TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value}


def _numeric_delta(left: Any, right: Any) -> float | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return round(float(right) - float(left), 4)
    return None


def _row_delta(rows: list[Row], metric: str) -> float | None:
    for row in rows:
        if row.get("metric") == metric and isinstance(row.get("delta"), (int, float)):
            return float(row["delta"])
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    # Stream the file handle instead of read_text().splitlines(), so a multi-GB log
    # is parsed line-by-line rather than held entirely in memory as a string + list.
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if value is None:
        return ""
    return str(value)


def _escape(value: str) -> str:
    return value.replace("|", "\\|")
