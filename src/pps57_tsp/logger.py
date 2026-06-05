#!/usr/bin/env python3
"""Logging JSONL e resumo para decisões/atuações TSP."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import IO, Dict, List, Optional

from .models import ActuationResult, DecisionStatus, TSPAction, TSPDecision


class TSPJsonlLogger:
    """JSONL writer com as mesmas garantias do CITSJsonlLogger: abre só no
    `__enter__` (sem leak), e usa line-buffering para sobreviver a crashes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: Optional[IO[str]] = None

    def write(self, item: TSPDecision | ActuationResult) -> None:
        if self._handle is None:
            raise RuntimeError("TSPJsonlLogger usado fora do context manager")
        self._handle.write(item.to_json() + "\n")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "TSPJsonlLogger":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", buffering=1)
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
    no_actuation_events = [item for item in actuations if item.no_actuation]
    real_applied = [item for item in applied if not item.no_actuation]
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
        "no_actuation_events": len(no_actuation_events),
        "real_traci_applied_events": len(real_applied),
    }


def write_tsp_summary(
    path: str | Path,
    decisions: List[TSPDecision],
    actuations: List[ActuationResult],
    extra: Dict[str, object] | None = None,
) -> Dict[str, object]:
    """Escrita atómica do resumo (`.tmp` + `os.replace`): crash a meio da
    serialização não deixa um JSON parcial no lugar do anterior."""
    summary = summarise_tsp(decisions, actuations)
    if extra:
        summary.update(extra)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, output_path)
    return summary
