#!/usr/bin/env python3
"""Logging JSONL e geração de resumo para a emulação C-ITS."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import IO, Dict, List, Optional

from .messages import CITSMessage, ResponseStatus
from .protocol_codec import JsonSimulationCodec, ProtocolCodec


class CITSJsonlLogger:
    """JSONL writer com garantias mínimas de auditoria.

    - Abre o ficheiro apenas em `__enter__` (sem leak se o context manager
      nunca for ativado).
    - Linha-buffered (`buffering=1`): em crash a meio da emulação, todas as
      linhas até à última `write()` ficam em disco.
    """

    def __init__(self, path: str | Path, codec: ProtocolCodec | None = None) -> None:
        self.path = Path(path)
        self.codec = codec or JsonSimulationCodec()
        self._handle: Optional[IO[str]] = None

    def write(self, message: CITSMessage) -> None:
        if self._handle is None:
            raise RuntimeError("CITSJsonlLogger usado fora do context manager")
        self._handle.write(self.codec.encode(message) + "\n")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "CITSJsonlLogger":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", buffering=1)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()


class IncrementalCITSSummary:
    """M2: agrega o resumo C-ITS sem manter a lista completa em memória.

    Equivalente em forma a `summarise_messages` mas incremental: cada mensagem
    é integrada via `add()`, permitindo corridas longas (>=7200 passos) sem
    crescimento O(n) de memória.
    """

    def __init__(self) -> None:
        self.total = 0
        self.by_type: Dict[str, int] = {}
        self.request_ids: set[str] = set()
        self.vehicle_ids: set[str] = set()
        self.rsu_ids: set[str] = set()
        self.processing = 0  # SSEM com response_status=processing (era "acknowledged")
        self.rejected = 0
        self.granted = 0
        self.cancelled = 0

    def add(self, message: CITSMessage) -> None:
        self.total += 1
        self.by_type[message.message_type] = self.by_type.get(message.message_type, 0) + 1
        # Identificadores nas mensagens novas: requestor.operational_vehicle_id
        # (SREM), rsu_id (MAPEM/SPATEM/SSEM), response.request_id (SSEM).
        requestor = getattr(message, "requestor", None)
        if requestor is not None:
            vehicle_id = getattr(requestor, "operational_vehicle_id", "")
            if vehicle_id:
                self.vehicle_ids.add(str(vehicle_id))
        rsu_id = getattr(message, "rsu_id", "")
        if rsu_id:
            self.rsu_ids.add(str(rsu_id))
        response = getattr(message, "response", None)
        if response is not None:
            request_id = getattr(response, "request_id", None)
            station_id = getattr(response, "requestor_station_id", None)
            if request_id is not None and station_id is not None:
                self.request_ids.add(f"{station_id}:{request_id}")
            status = getattr(response, "response_status", None)
            if status == ResponseStatus.PROCESSING.value:
                self.processing += 1
            elif status == ResponseStatus.REJECTED.value:
                self.rejected += 1
            elif status == ResponseStatus.GRANTED.value:
                self.granted += 1
            elif status == ResponseStatus.UNKNOWN.value:
                # cancelamento devolvido pelo RSU como ack idempotente
                self.cancelled += 1

    def to_dict(self) -> Dict[str, object]:
        return {
            "total_messages": self.total,
            "by_type": dict(self.by_type),
            "unique_request_ids": len(self.request_ids),
            "unique_vehicle_ids": len(self.vehicle_ids),
            "unique_rsu_ids": len(self.rsu_ids),
            "processing_messages": self.processing,
            # Legacy alias for v0.3 reports and comparison tools. In v0.4 the
            # standard-aligned SSEM status is `processing`, not `acknowledged`.
            "acknowledged_messages": self.processing,
            "rejected_messages": self.rejected,
            "granted_messages": self.granted,
            "cancelled_acks": self.cancelled,
        }


def summarise_messages(messages: List[CITSMessage]) -> Dict[str, object]:
    """Resumo batch das mensagens C-ITS.

    É um wrapper fino sobre `IncrementalCITSSummary` para que as duas vias
    (batch e incremental) não possam divergir — antes eram duas
    implementações independentes da mesma agregação.
    """
    summary = IncrementalCITSSummary()
    for message in messages:
        summary.add(message)
    return summary.to_dict()


def write_summary_dict(
    path: str | Path, summary: Dict[str, object], extra: Dict[str, object] | None = None
) -> Dict[str, object]:
    """Escreve um resumo já calculado, normalmente agregado incrementalmente.

    Escreve atomicamente via `.tmp` + `os.replace`: um crash a meio da
    serialização deixa o ficheiro anterior intacto, em vez de um JSON parcial
    que envenenaria o leitor seguinte.
    """
    summary = dict(summary)
    if extra:
        summary.update(extra)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, output_path)
    return summary
