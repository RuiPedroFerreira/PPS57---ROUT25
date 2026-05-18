#!/usr/bin/env python3
"""Logging JSONL e geração de resumo para a emulação C-ITS."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

from .messages import CITSMessage, RequestStatus


class CITSJsonlLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")

    def write(self, message: CITSMessage) -> None:
        self._handle.write(message.to_json() + "\n")

    def write_many(self, messages: Iterable[CITSMessage]) -> None:
        for message in messages:
            self.write(message)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "CITSJsonlLogger":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()


def summarise_messages(messages: List[CITSMessage]) -> Dict[str, object]:
    by_type: Dict[str, int] = {}
    acknowledged = 0
    rejected = 0
    request_ids = set()
    vehicle_ids = set()
    rsu_ids = set()

    for message in messages:
        by_type[message.message_type] = by_type.get(message.message_type, 0) + 1
        payload = message.to_dict()
        if "request_id" in payload:
            request_ids.add(payload["request_id"])
        if "vehicle_id" in payload and payload["vehicle_id"]:
            vehicle_ids.add(payload["vehicle_id"])
        if "rsu_id" in payload and payload["rsu_id"]:
            rsu_ids.add(payload["rsu_id"])
        if payload.get("status") == RequestStatus.ACKNOWLEDGED.value:
            acknowledged += 1
        if payload.get("status") == RequestStatus.REJECTED.value:
            rejected += 1

    return {
        "total_messages": len(messages),
        "by_type": by_type,
        "unique_request_ids": len(request_ids),
        "unique_vehicle_ids": len(vehicle_ids),
        "unique_rsu_ids": len(rsu_ids),
        "acknowledged_messages": acknowledged,
        "rejected_messages": rejected,
    }


def write_summary(path: str | Path, messages: List[CITSMessage], extra: Dict[str, object] | None = None) -> Dict[str, object]:
    summary = summarise_messages(messages)
    if extra:
        summary.update(extra)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
