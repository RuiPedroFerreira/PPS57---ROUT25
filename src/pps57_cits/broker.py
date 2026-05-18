#!/usr/bin/env python3
"""Broker em memória para emular a troca de mensagens OBU/RSU."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import DefaultDict, Dict, Iterable, List

from .messages import CITSMessage


@dataclass
class InMemoryMessageBroker:
    """Broker simples para o Pacote 3.

    Mantém filas por destino e histórico global. Em operação real, este módulo
    seria substituído por uma stack ITS-G5/C-V2X ou por middleware de integração.
    """

    queues: DefaultDict[str, List[CITSMessage]] = field(default_factory=lambda: defaultdict(list))
    history: List[CITSMessage] = field(default_factory=list)

    def publish(self, message: CITSMessage) -> None:
        self.queues[message.destination_id].append(message)
        self.history.append(message)

    def publish_many(self, messages: Iterable[CITSMessage]) -> None:
        for message in messages:
            self.publish(message)

    def consume(self, destination_id: str) -> List[CITSMessage]:
        messages = self.queues.get(destination_id, [])
        self.queues[destination_id] = []
        return messages

    def peek(self, destination_id: str) -> List[CITSMessage]:
        return list(self.queues.get(destination_id, []))

    def count_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for message in self.history:
            counts[message.message_type] = counts.get(message.message_type, 0) + 1
        return counts
