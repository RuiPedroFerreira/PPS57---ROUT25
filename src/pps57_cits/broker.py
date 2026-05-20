#!/usr/bin/env python3
"""Broker em memória para emular a troca de mensagens OBU/RSU."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import DefaultDict, Dict, Iterable, List

from .messages import CITSMessage


@dataclass
class InMemoryMessageBroker:
    """Simple C-ITS message broker.

    M2: NÃO mantém histórico ilimitado de mensagens (corridas SUMO longas
    acumulavam até dezenas de milhares de objetos). Em vez disso mantém apenas
    contadores incrementais por tipo. Filas por destino devem ser explicitamente
    drenadas (consume/drain) — destinos não-RSU (OBU, BROADCAST) que não são
    consumidos podem ser drenados periodicamente para não crescer.
    """

    queues: DefaultDict[str, List[CITSMessage]] = field(default_factory=lambda: defaultdict(list))
    _counts: Dict[str, int] = field(default_factory=dict)

    def publish(self, message: CITSMessage) -> None:
        self.queues[message.destination_id].append(message)
        self._counts[message.message_type] = self._counts.get(message.message_type, 0) + 1

    def publish_many(self, messages: Iterable[CITSMessage]) -> None:
        for message in messages:
            self.publish(message)

    def consume(self, destination_id: str) -> List[CITSMessage]:
        messages = self.queues.get(destination_id, [])
        self.queues[destination_id] = []
        return messages

    def drain(self, destination_id: str) -> int:
        """Descarta a fila de um destino. Devolve quantas mensagens foram descartadas."""
        n = len(self.queues.get(destination_id, []))
        self.queues[destination_id] = []
        return n

    def drain_all_except(self, keep_destinations: Iterable[str]) -> int:
        keep = set(keep_destinations)
        dropped = 0
        for dest in list(self.queues.keys()):
            if dest not in keep:
                dropped += self.drain(dest)
        return dropped

    def peek(self, destination_id: str) -> List[CITSMessage]:
        return list(self.queues.get(destination_id, []))

    def count_by_type(self) -> Dict[str, int]:
        return dict(self._counts)
