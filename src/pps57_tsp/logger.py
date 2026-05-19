#!/usr/bin/env python3
"""Logging JSONL e resumo para decisões/atuações TSP."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

from .models import ActuationResult, DecisionStatus, TSPAction, TSPDecision


class TSPJsonlLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")

    def write(self, item: TSPDecision | ActuationResult) -> None:
        self._handle.write(item.to_json() + "\n")

    def write_many(self, items: Iterable[TSPDecision | ActuationResult]) -> None:
        for item in items:
            self.write(item)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TSPJsonlLogger":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()


def summarise_tsp(decisions: List[TSPDecision], actuations: List[ActuationResult]) -> Dict[str, object]:
    by_action: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for decision in decisions:
        by_action[decision.action] = by_action.get(decision.action, 0) + 1
        by_status[decision.status] = by_status.get(decision.status, 0) + 1

    applied = [item for item in actuations if item.applied]
    dry_run_applied = [item for item in applied if item.dry_run]
    real_applied = [item for item in applied if not item.dry_run]
    blocked = [item for item in decisions if item.status == DecisionStatus.BLOCKED_BY_SAFETY.value]

    return {
        "total_decisions": len(decisions),
        "by_action": by_action,
        "by_status": by_status,
        "approved_decisions": by_status.get(DecisionStatus.APPROVED.value, 0),
        "blocked_by_safety": len(blocked),
        "green_extension_decisions": by_action.get(TSPAction.GREEN_EXTENSION.value, 0),
        "early_green_decisions": by_action.get(TSPAction.EARLY_GREEN.value, 0),
        "no_action_decisions": by_action.get(TSPAction.NO_ACTION.value, 0),
        "reevaluate_decisions": by_action.get(TSPAction.REEVALUATE_NEXT_CYCLE.value, 0),
        "actuation_events": len(actuations),
        "applied_events": len(applied),
        "dry_run_applied_events": len(dry_run_applied),
        "real_traci_applied_events": len(real_applied),
    }


def write_tsp_summary(
    path: str | Path,
    decisions: List[TSPDecision],
    actuations: List[ActuationResult],
    extra: Dict[str, object] | None = None,
) -> Dict[str, object]:
    summary = summarise_tsp(decisions, actuations)
    if extra:
        summary.update(extra)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
