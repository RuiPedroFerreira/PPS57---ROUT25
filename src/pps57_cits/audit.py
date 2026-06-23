#!/usr/bin/env python3
"""Audit/replay helpers for C-ITS JSONL protocol lifecycles."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any

from .lifecycle import PriorityRequestState, transition_request_state

# Fail-closed protocol KPIs (B30): a non-zero count in any of these means the
# audited lifecycle has integrity problems — a final SSEM was never delivered for
# a live request, an illegal state transition occurred, an SSEM was delivered for a
# request with no preceding SREM (orphan), or the controller reported an actuation
# error. The audit CLI must exit non-zero when any are present.
#
# `controller_nacks` is deliberately NOT here: a NACK is the safety layer
# legitimately rejecting an unsafe/late request (normal operation), not a protocol
# integrity defect — gating on it would fail every run where the safety veto fires.
PROTOCOL_VIOLATION_KEYS = (
    "missing_final_ssem",
    "invalid_state_transitions",
    "orphan_ssem",
    "actuation_errors",
)


def lifecycle_violations(report: dict[str, Any]) -> dict[str, int]:
    """Return the subset of fail-closed protocol KPIs that are non-zero.

    Empty result == clean lifecycle. A non-empty mapping (key -> count) means the
    audit must be treated as a failure by callers.
    """
    kpis = report.get("protocol_kpis", {}) if isinstance(report, dict) else {}
    violations: dict[str, int] = {}
    for key in PROTOCOL_VIOLATION_KEYS:
        try:
            count = int(kpis.get(key, 0) or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            violations[key] = count
    return violations


def audit_protocol_lifecycle(
    cits_messages_path: str | Path,
    tsp_decisions_path: str | Path | None = None,
    actuation_path: str | Path | None = None,
) -> dict[str, Any]:
    """Reconstruct SREM/SSEM/TSP/actuation chains from JSONL artifacts."""
    cits_messages = _read_jsonl(cits_messages_path)
    decisions = _read_jsonl(tsp_decisions_path) if tsp_decisions_path else []
    actuations = _read_jsonl(actuation_path) if actuation_path else []

    chains: dict[str, dict[str, Any]] = {}
    srem_key_counts: Counter[str] = Counter()
    final_statuses: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    duplicate_reasons: Counter[str] = Counter()
    invalid_transitions: list[dict[str, str]] = []
    orphan_ssem = 0

    for message in cits_messages:
        message_type = _normalise_message_type(message)
        if message_type == "SREM":
            key = _request_key_from_srem(message)
            if key is None:
                continue
            srem_key_counts[key] += 1
            chain = chains.setdefault(key, _new_chain(key))
            chain["srem_count"] += 1
            chain["srem_message_ids"].append(message.get("message_id"))
            chain["request_type"] = _srem_request_type(message)
            if chain["first_srem_generation_time_ms"] is None:
                chain["first_srem_generation_time_ms"] = _generation_time_ms(message)
            if _srem_request_type(message) == "priorityCancellation":
                _transition_chain(chain, PriorityRequestState.CANCELLED.value, invalid_transitions)
            continue

        if message_type != "SSEM":
            continue
        key = _request_key_from_ssem(message)
        if key is None:
            continue
        chain = chains.get(key)
        if chain is None:
            orphan_ssem += 1
            chain = chains.setdefault(key, _new_chain(key))
            chain["orphan"] = True
        status = _ssem_status(message)
        chain["ssem_count"] += 1
        chain["ssem_statuses"].append(status)
        if status == "processing":
            chain["processing_ssem_count"] += 1
            if chain["processing_generation_time_ms"] is None:
                chain["processing_generation_time_ms"] = _generation_time_ms(message)
            _transition_chain(chain, PriorityRequestState.PROCESSING.value, invalid_transitions)
        elif status in {"granted", "rejected"}:
            chain["final_ssem_count"] += 1
            chain["final_generation_time_ms"] = _generation_time_ms(message)
            chain["final_status"] = status
            final_statuses[status] += 1
            if status == "rejected":
                reason = str(message.get("audit", {}).get("rejection_reason") or "unspecified")
                rejection_reasons[reason] += 1
                if "duplicate" in reason or "out_of_order" in reason:
                    duplicate_reasons[reason] += 1
            target = (
                PriorityRequestState.GRANTED.value
                if status == "granted"
                else PriorityRequestState.REJECTED.value
            )
            _transition_chain(chain, target, invalid_transitions)
        elif status == "unknown":
            reason = str(message.get("audit", {}).get("rejection_reason") or "")
            if reason == "priority_request_cancelled":
                chain["final_ssem_count"] += 1
                chain["final_generation_time_ms"] = _generation_time_ms(message)
                chain["final_status"] = "cancelled"
                final_statuses["cancelled"] += 1
                _transition_chain(chain, PriorityRequestState.CANCELLED.value, invalid_transitions)

    decisions_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        request_id = decision.get("request_id")
        if request_id:
            decisions_by_request[str(request_id)].append(decision)

    actuations_by_decision = {
        str(item.get("decision_id")): item for item in actuations if item.get("decision_id")
    }
    controller_nacks = 0
    actuation_errors = 0
    for items in decisions_by_request.values():
        for decision in items:
            actuation = actuations_by_decision.get(str(decision.get("decision_id")))
            if not actuation:
                continue
            reason = str(actuation.get("reason", ""))
            if actuation.get("severity") == "error":
                actuation_errors += 1
            if not actuation.get("applied") and not actuation.get("no_actuation") and reason:
                controller_nacks += 1

    for key, chain in chains.items():
        linked_decisions = decisions_by_request.get(key, [])
        chain["decision_count"] = len(linked_decisions)
        chain["actuation_count"] = sum(
            1
            for decision in linked_decisions
            if str(decision.get("decision_id")) in actuations_by_decision
        )

    processing_latencies = [
        chain["processing_generation_time_ms"] - chain["first_srem_generation_time_ms"]
        for chain in chains.values()
        if chain["first_srem_generation_time_ms"] is not None
        and chain["processing_generation_time_ms"] is not None
    ]
    final_latencies = [
        chain["final_generation_time_ms"] - chain["first_srem_generation_time_ms"]
        for chain in chains.values()
        if chain["first_srem_generation_time_ms"] is not None
        and chain["final_generation_time_ms"] is not None
    ]
    # As cadeias são por (station:request:seq), mas a OBU incrementa o
    # sequence_number em cada priorityRequestUpdate: só a seq mais recente de
    # cada pedido recebe o SSEM final. As seqs anteriores ficam marcadas como
    # supersedidas (contadas à parte) em vez de inflacionar missing_final_ssem.
    latest_sequence_by_request: dict[str, int] = {}
    for key, chain in chains.items():
        if chain["srem_count"] == 0:
            continue
        request_scope, sequence = _split_request_key(key)
        if request_scope is None or sequence is None:
            continue
        current = latest_sequence_by_request.get(request_scope)
        if current is None or sequence > current:
            latest_sequence_by_request[request_scope] = sequence
    superseded_chains = 0
    for key, chain in chains.items():
        request_scope, sequence = _split_request_key(key)
        chain["superseded_by_newer_sequence"] = (
            request_scope is not None
            and sequence is not None
            and sequence < latest_sequence_by_request.get(request_scope, sequence)
        )
        if chain["superseded_by_newer_sequence"]:
            superseded_chains += 1
    missing_final = [
        key
        for key, chain in chains.items()
        if chain["srem_count"] > 0
        and chain["final_ssem_count"] == 0
        and chain["state"] not in {PriorityRequestState.CANCELLED.value}
        and not chain["superseded_by_newer_sequence"]
    ]

    return {
        "artifact_paths": {
            "cits_messages": str(cits_messages_path),
            "tsp_decisions": str(tsp_decisions_path) if tsp_decisions_path else None,
            "actuations": str(actuation_path) if actuation_path else None,
        },
        "protocol_kpis": {
            "total_cits_messages": len(cits_messages),
            "total_srem": sum(
                1 for item in cits_messages if _normalise_message_type(item) == "SREM"
            ),
            "total_ssem": sum(
                1 for item in cits_messages if _normalise_message_type(item) == "SSEM"
            ),
            "lifecycle_chains": len(chains),
            "duplicate_srem_keys": sum(1 for count in srem_key_counts.values() if count > 1),
            "with_processing_ssem": sum(
                1 for chain in chains.values() if chain["processing_ssem_count"] > 0
            ),
            "with_final_ssem": sum(1 for chain in chains.values() if chain["final_ssem_count"] > 0),
            "missing_final_ssem": len(missing_final),
            "superseded_request_chains": superseded_chains,
            "orphan_ssem": orphan_ssem,
            "invalid_state_transitions": len(invalid_transitions),
            "controller_nacks": controller_nacks,
            "actuation_errors": actuation_errors,
        },
        "latency_ms": {
            "srem_to_processing": _latency_summary(processing_latencies),
            "srem_to_final": _latency_summary(final_latencies),
        },
        "final_ssem_by_status": dict(final_statuses),
        "rejections_by_reason": dict(rejection_reasons),
        "duplicate_or_ordering_rejections": dict(duplicate_reasons),
        "missing_final_request_keys": missing_final,
        "invalid_transitions": invalid_transitions,
        "chains": chains,
    }


def _read_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    jsonl_path = Path(path)
    if not jsonl_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _new_chain(key: str) -> dict[str, Any]:
    return {
        "request_key": key,
        "state": PriorityRequestState.CREATED.value,
        "orphan": False,
        "request_type": None,
        "srem_count": 0,
        "ssem_count": 0,
        "processing_ssem_count": 0,
        "final_ssem_count": 0,
        "final_status": None,
        "decision_count": 0,
        "actuation_count": 0,
        "srem_message_ids": [],
        "ssem_statuses": [],
        "first_srem_generation_time_ms": None,
        "processing_generation_time_ms": None,
        "final_generation_time_ms": None,
    }


def _transition_chain(
    chain: dict[str, Any],
    target: str,
    invalid_transitions: list[dict[str, str]],
) -> None:
    current = str(chain["state"])
    try:
        chain["state"] = transition_request_state(current, target)
    except ValueError:
        invalid_transitions.append(
            {"request_key": str(chain["request_key"]), "transition": f"{current}->{target}"}
        )


def _normalise_message_type(item: dict[str, Any]) -> str:
    raw = str(item.get("message_type") or "")
    return raw[:-5] if raw.endswith("_like") else raw


def _split_request_key(key: str) -> tuple[str | None, int | None]:
    """Divide "station:request:seq" em ("station:request", seq)."""
    head, sep, sequence_text = str(key).rpartition(":")
    if not sep:
        return None, None
    try:
        return head, int(sequence_text)
    except ValueError:
        return None, None


def _request_key_from_srem(item: dict[str, Any]) -> str | None:
    requests = item.get("requests") or []
    if not requests or not isinstance(requests[0], dict):
        return None
    return f"{item.get('station_id')}:{requests[0].get('request_id')}:{item.get('sequence_number')}"


def _request_key_from_ssem(item: dict[str, Any]) -> str | None:
    response = item.get("response") or {}
    if not isinstance(response, dict):
        return None
    return (
        f"{response.get('requestor_station_id')}:"
        f"{response.get('request_id')}:"
        f"{response.get('sequence_number')}"
    )


def _srem_request_type(item: dict[str, Any]) -> str:
    requests = item.get("requests") or []
    if not requests or not isinstance(requests[0], dict):
        return ""
    return str(requests[0].get("request_type") or "")


def _ssem_status(item: dict[str, Any]) -> str:
    response = item.get("response") or {}
    if isinstance(response, dict):
        return str(response.get("response_status") or item.get("status") or "")
    return str(item.get("status") or "")


def _generation_time_ms(item: dict[str, Any]) -> int | None:
    security = item.get("security") or {}
    if isinstance(security, dict) and isinstance(security.get("generation_time_ms"), int):
        return int(security["generation_time_ms"])
    moy = item.get("moy")
    timestamp_ms = item.get("timestamp_ms")
    if isinstance(moy, int) and isinstance(timestamp_ms, int):
        return (moy * 60000) + timestamp_ms
    return None


def _latency_summary(values: Iterable[int]) -> dict[str, float | None]:
    items = list(values)
    if not items:
        return {"count": 0, "avg": None, "min": None, "max": None}
    return {
        "count": len(items),
        "avg": round(float(mean(items)), 3),
        "min": float(min(items)),
        "max": float(max(items)),
    }
