#!/usr/bin/env python3
"""Load and aggregate PPS57 artifacts for the validation platform.

The module deliberately depends only on the Python standard library so the
command-line checks and unit tests can run without Streamlit or SUMO installed.
The Streamlit app imports this module and renders the returned snapshot.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
# M4: defusedxml bloqueia XXE/billion-laughs; ingerimos tripinfo de fontes
# potencialmente externas, portanto não usamos o parser stdlib aqui.
from defusedxml import ElementTree as ET  # type: ignore[import-untyped]

DEFAULT_ARTIFACTS: Dict[str, str] = {
    "cits_messages": "outputs/cits_messages.jsonl",
    "tsp_decisions": "outputs/tsp_decisions.jsonl",
    "tsp_actuation": "outputs/tsp_actuation.jsonl",
    "offline_samples": "outputs/pacote5_offline_samples.jsonl",
    "policy_candidates": "outputs/pacote5_policy_candidates.jsonl",
    "cits_summary": "reports/cits_emulation_summary.json",
    "tsp_summary": "reports/tsp_emulation_summary.json",
    "baseline_kpis": "reports/baseline_kpis.json",
    "optimization_summary": "reports/pacote5_optimization_summary.json",
    "policy_report": "reports/pacote5_policy_report.json",
    "tripinfo": "outputs/tripinfo.xml",
}

DEFAULT_CRITICAL_ARTIFACTS = [
    "cits_messages",
    "tsp_decisions",
    "tsp_actuation",
    "tsp_summary",
    "optimization_summary",
]

JSONL_ARTIFACTS = {
    "cits_messages",
    "tsp_decisions",
    "tsp_actuation",
    "offline_samples",
    "policy_candidates",
}

JSON_ARTIFACTS = {
    "cits_summary",
    "tsp_summary",
    "baseline_kpis",
    "optimization_summary",
    "policy_report",
}


@dataclass(frozen=True)
class ArtifactStatus:
    """Availability metadata for one expected artifact."""

    key: str
    label: str
    path: str
    exists: bool
    size_bytes: int = 0
    record_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "path": self.path,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "error": self.error,
        }


def load_platform_config(root: Path, config_path: str | Path = "configs/platform_config.json") -> Dict[str, Any]:
    """Load platform configuration, falling back to safe defaults."""
    path = _resolve(root, config_path)
    if not path.exists():
        return {
            "package_id": "PPS57_PACKAGE_6_PLATFORM",
            "version": "0.6.0",
            "scenario_id": "unknown",
            "title": "PPS57 — ROUT25 Traffic Priority Platform",
            "artifacts": dict(DEFAULT_ARTIFACTS),
            "critical_artifacts": list(DEFAULT_CRITICAL_ARTIFACTS),
            "labels": {},
            "dashboard": {"max_records_loaded": 5000},
        }
    # Resiliente (não rebenta a dashboard) mas a corrupção deixa de ser
    # silenciosa: um config inválido devolve defaults + config_error visível.
    config_error: Optional[str] = None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            config_error = f"platform config não é um objeto JSON: {path}"
            payload = {}
    except (OSError, json.JSONDecodeError) as exc:
        config_error = f"platform config inválido ({path}): {exc}"
        payload = {}
    payload.setdefault("artifacts", dict(DEFAULT_ARTIFACTS))
    payload.setdefault("critical_artifacts", list(DEFAULT_CRITICAL_ARTIFACTS))
    payload.setdefault("labels", {})
    payload.setdefault("dashboard", {})
    if config_error:
        payload["config_error"] = config_error
    return payload


def collect_snapshot(
    root: Path,
    config_path: str | Path = "configs/platform_config.json",
    max_records: Optional[int] = None,
) -> Dict[str, Any]:
    """Collect all available PPS57 artifacts and derived dashboard metrics."""
    root = Path(root).resolve()
    config = load_platform_config(root, config_path)
    artifact_paths: Mapping[str, str] = {**DEFAULT_ARTIFACTS, **config.get("artifacts", {})}
    labels: Mapping[str, str] = config.get("labels", {})
    dashboard_cfg: Mapping[str, Any] = config.get("dashboard", {})
    if max_records is None:
        max_records = _safe_int(dashboard_cfg.get("max_records_loaded"), default=5000)

    jsonl_records: Dict[str, List[Dict[str, Any]]] = {}
    json_payloads: Dict[str, Dict[str, Any]] = {}
    statuses: List[ArtifactStatus] = []
    statuses_by_key: Dict[str, ArtifactStatus] = {}
    tripinfo_summary: Dict[str, Any] = {}

    for key, rel_path in artifact_paths.items():
        path = _resolve(root, rel_path)
        label = labels.get(key, key)
        if key in JSONL_ARTIFACTS:
            records, status = read_jsonl_with_status(key, label, path, max_records=max_records)
            jsonl_records[key] = records
            statuses.append(status)
        elif key in JSON_ARTIFACTS:
            payload, status = read_json_with_status(key, label, path)
            json_payloads[key] = payload
            statuses.append(status)
        elif key == "tripinfo":
            tripinfo_summary, status = parse_tripinfo_with_status(key, label, path)
            statuses.append(status)
        else:
            status = file_status(key, label, path)
            statuses.append(status)
        statuses_by_key[key] = status

    aggregates = build_aggregates(jsonl_records, json_payloads, tripinfo_summary, statuses_by_key)
    status_payload = [item.to_dict() for item in statuses]
    missing_critical = [
        item.key for item in statuses if item.key in set(config.get("critical_artifacts", [])) and not item.exists
    ]

    return {
        "root": str(root),
        "config": config,
        "config_error": config.get("config_error"),
        "artifacts": status_payload,
        "missing_critical_artifacts": missing_critical,
        "records": jsonl_records,
        "reports": json_payloads,
        "tripinfo": tripinfo_summary,
        "aggregates": aggregates,
    }


def build_aggregates(
    jsonl_records: Mapping[str, List[Dict[str, Any]]],
    json_payloads: Mapping[str, Dict[str, Any]],
    tripinfo_summary: Mapping[str, Any],
    artifact_statuses: Optional[Mapping[str, ArtifactStatus]] = None,
) -> Dict[str, Any]:
    """Compute cross-package metrics from logs and reports."""
    cits_messages = valid_jsonl_records(jsonl_records.get("cits_messages", []))
    tsp_decisions = valid_jsonl_records(jsonl_records.get("tsp_decisions", []))
    tsp_actuation = valid_jsonl_records(jsonl_records.get("tsp_actuation", []))
    policy_candidates = valid_jsonl_records(jsonl_records.get("policy_candidates", []))
    offline_samples = valid_jsonl_records(jsonl_records.get("offline_samples", []))

    ssem_messages = [item for item in cits_messages if item.get("message_type") == "SSEM_like"]
    srem_messages = [item for item in cits_messages if item.get("message_type") == "SREM_like"]
    applied_actuations = [item for item in tsp_actuation if _safe_bool(item.get("applied"))]
    blocked_decisions = [item for item in tsp_decisions if item.get("status") == "blocked_by_safety"]
    selected_candidates = [item for item in policy_candidates if _safe_bool(item.get("selected"))]
    unsafe_candidates = [
        item
        for item in policy_candidates
        if item.get("safety_status") == "blocked_by_safety" or _safe_bool(item.get("is_safety_blocked"))
    ]

    cits_summary = json_payloads.get("cits_summary", {})
    tsp_summary = json_payloads.get("tsp_summary", {})
    optimization_summary = json_payloads.get("optimization_summary", {})

    total_messages = _count_with_summary_fallback(
        "cits_messages", len(cits_messages), cits_summary.get("total_messages"), artifact_statuses
    )
    total_decisions = _count_with_summary_fallback(
        "tsp_decisions", len(tsp_decisions), tsp_summary.get("total_decisions"), artifact_statuses
    )
    total_actuations = _count_with_summary_fallback(
        "tsp_actuation", len(tsp_actuation), tsp_summary.get("actuation_events"), artifact_statuses
    )
    optimization_delta = _safe_float(optimization_summary.get("reward_delta"), default=0.0)

    return {
        "overview": {
            "total_cits_messages": total_messages,
            "total_tsp_decisions": total_decisions,
            "total_actuation_events": total_actuations,
            "applied_actuation_events": _count_with_summary_fallback(
                "tsp_actuation", len(applied_actuations), tsp_summary.get("applied_events"), artifact_statuses
            ),
            "blocked_by_safety": _count_with_summary_fallback(
                "tsp_decisions", len(blocked_decisions), tsp_summary.get("blocked_by_safety"), artifact_statuses
            ),
            "offline_sample_count": _count_with_summary_fallback(
                "offline_samples", len(offline_samples), optimization_summary.get("scenario_count"), artifact_statuses
            ),
            "policy_candidate_count": _count_with_summary_fallback(
                "policy_candidates", len(policy_candidates), optimization_summary.get("candidate_count"), artifact_statuses
            ),
            "unsafe_candidates_filtered": _count_with_summary_fallback(
                "policy_candidates",
                len(unsafe_candidates),
                optimization_summary.get("unsafe_candidates_filtered"),
                artifact_statuses,
            ),
            "reward_delta": optimization_delta,
            "tripinfo_vehicle_count": _safe_int(tripinfo_summary.get("vehicle_count"), default=0),
            "tripinfo_avg_duration_s": _safe_float(tripinfo_summary.get("avg_duration_s"), default=0.0),
        },
        "cits": {
            "by_message_type": count_by(cits_messages, "message_type", fallback=cits_summary.get("by_type")),
            "by_status": count_by(ssem_messages, "status"),
            "by_action": count_by(ssem_messages, "action"),
            "requests_by_vehicle": count_by(srem_messages, "vehicle_id"),
            "requests_by_rsu": count_by(srem_messages, "rsu_id"),
        },
        "tsp": {
            "by_action": count_by(tsp_decisions, "action", fallback=tsp_summary.get("by_action")),
            "by_status": count_by(tsp_decisions, "status", fallback=tsp_summary.get("by_status")),
            "by_rsu": count_by(tsp_decisions, "rsu_id"),
            "by_vehicle": count_by(tsp_decisions, "vehicle_id"),
            "by_reason": count_by(tsp_decisions, "reason"),
        },
        "actuation": {
            "by_action": count_by(tsp_actuation, "action"),
            "by_command": count_by(tsp_actuation, "command"),
            "by_applied": count_by_bool(tsp_actuation, "applied"),
            "by_dry_run": count_by_bool(tsp_actuation, "dry_run"),
            "by_tls": count_by(tsp_actuation, "tls_id"),
        },
        "optimization": {
            "selected_by_action": count_by(selected_candidates, "action", fallback=optimization_summary.get("selected_by_action")),
            "baseline_by_action": optimization_summary.get("baseline_by_action", {}),
            "candidates_by_action": count_by(policy_candidates, "action"),
            "candidates_by_safety_status": count_by(policy_candidates, "safety_status"),
            "reward_delta": optimization_delta,
            "baseline_reward": _safe_float(optimization_summary.get("baseline_reward"), default=0.0),
            "optimized_reward": _safe_float(optimization_summary.get("optimized_reward"), default=0.0),
        },
    }


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def read_json_with_status(key: str, label: str, path: Path) -> Tuple[Dict[str, Any], ArtifactStatus]:
    if not path.exists():
        return {}, ArtifactStatus(key=key, label=label, path=str(path), exists=False)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return payload, ArtifactStatus(
            key=key,
            label=label,
            path=str(path),
            exists=True,
            size_bytes=path.stat().st_size,
            record_count=1,
        )
    except Exception as exc:  # pragma: no cover - defensive I/O path
        return {}, ArtifactStatus(
            key=key,
            label=label,
            path=str(path),
            exists=True,
            size_bytes=path.stat().st_size,
            error=str(exc),
        )


def read_jsonl(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records, _ = read_jsonl_with_status("jsonl", "jsonl", path, max_records=max_records)
    return records


def read_jsonl_with_status(
    key: str,
    label: str,
    path: Path,
    max_records: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], ArtifactStatus]:
    if not path.exists():
        return [], ArtifactStatus(key=key, label=label, path=str(path), exists=False)

    records: List[Dict[str, Any]] = []
    error: Optional[str] = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if max_records is not None and len(records) >= max_records:
                    break
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        records.append(payload)
                    else:
                        records.append({"value": payload})
                except json.JSONDecodeError as exc:
                    records.append({"__parse_error__": str(exc), "__line_number__": line_number, "raw": raw})
                    error = f"JSONL parse error at line {line_number}: {exc}"
    except Exception as exc:  # pragma: no cover - defensive I/O path
        error = str(exc)

    return records, ArtifactStatus(
        key=key,
        label=label,
        path=str(path),
        exists=True,
        size_bytes=path.stat().st_size,
        record_count=len(records),
        error=error,
    )


def parse_tripinfo_with_status(key: str, label: str, path: Path) -> Tuple[Dict[str, Any], ArtifactStatus]:
    if not path.exists():
        return {}, ArtifactStatus(key=key, label=label, path=str(path), exists=False)
    try:
        summary = parse_tripinfo(path)
        return summary, ArtifactStatus(
            key=key,
            label=label,
            path=str(path),
            exists=True,
            size_bytes=path.stat().st_size,
            record_count=_safe_int(summary.get("vehicle_count"), default=0),
        )
    except Exception as exc:  # pragma: no cover - defensive I/O path
        return {}, ArtifactStatus(
            key=key,
            label=label,
            path=str(path),
            exists=True,
            size_bytes=path.stat().st_size,
            error=str(exc),
        )


def parse_tripinfo(path: Path) -> Dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    durations: List[float] = []
    route_lengths: List[float] = []
    waiting_times: List[float] = []
    for trip in root.findall("tripinfo"):
        durations.append(_safe_float(trip.attrib.get("duration"), default=0.0))
        route_lengths.append(_safe_float(trip.attrib.get("routeLength"), default=0.0))
        waiting_times.append(_safe_float(trip.attrib.get("waitingTime"), default=0.0))
    return {
        "vehicle_count": len(durations),
        "avg_duration_s": _avg(durations),
        "max_duration_s": max(durations) if durations else 0.0,
        "avg_route_length_m": _avg(route_lengths),
        "avg_waiting_time_s": _avg(waiting_times),
        "max_waiting_time_s": max(waiting_times) if waiting_times else 0.0,
    }


def file_status(key: str, label: str, path: Path) -> ArtifactStatus:
    return ArtifactStatus(
        key=key,
        label=label,
        path=str(path),
        exists=path.exists(),
        size_bytes=path.stat().st_size if path.exists() else 0,
    )


def count_by(records: Iterable[Mapping[str, Any]], key: str, fallback: Any = None) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for item in records:
        value = item.get(key)
        if value is None:
            continue
        counter[str(value)] += 1
    if counter:
        return dict(sorted(counter.items()))
    if isinstance(fallback, dict):
        return {str(k): _safe_int(v, default=0) for k, v in sorted(fallback.items())}
    return {}


def count_by_bool(records: Iterable[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for item in records:
        value = item.get(key)
        if value is None:
            continue
        counter["true" if _safe_bool(value) else "false"] += 1
    return dict(sorted(counter.items()))


def valid_jsonl_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter synthetic parse-error rows before computing operational KPIs."""
    return [item for item in records if "__parse_error__" not in item]


def latest_records(records: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    """Return the last N records without mutating the original list."""
    if limit <= 0:
        return []
    return list(records[-limit:])


def export_snapshot(snapshot: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _resolve(root: Path, path: str | Path) -> Path:
    item = Path(path)
    if item.is_absolute():
        return item
    return root / item


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n", ""}:
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _count_with_summary_fallback(
    artifact_key: str,
    computed_count: int,
    fallback_value: Any,
    artifact_statuses: Optional[Mapping[str, ArtifactStatus]],
) -> int:
    status = artifact_statuses.get(artifact_key) if artifact_statuses is not None else None
    if status is not None and status.exists:
        return computed_count
    return _safe_int(fallback_value, default=computed_count)


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
