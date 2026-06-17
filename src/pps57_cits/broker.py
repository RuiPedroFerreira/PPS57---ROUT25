#!/usr/bin/env python3
"""Broker em memória para emular a troca de mensagens OBU/RSU."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .messages import CITSMessage
from .protocol_codec import JsonSimulationCodec, ProtocolCodec

WirePayload = CITSMessage | str


@dataclass(frozen=True)
class PendingDelivery:
    due_step: int
    destination_id: str
    payload: WirePayload


@dataclass
class InMemoryMessageBroker:
    """Simple C-ITS message broker.

    M2: NÃO mantém histórico ilimitado de mensagens (corridas SUMO longas
    acumulavam até dezenas de milhares de objetos). Em vez disso mantém apenas
    contadores incrementais por tipo. Filas por destino devem ser explicitamente
    drenadas (consume/drain) — destinos não-RSU (OBU, BROADCAST) que não são
    consumidos podem ser drenados periodicamente para não crescer.
    """

    transport_config: dict[str, object] = field(default_factory=dict)
    codec: ProtocolCodec | None = None
    queues: defaultdict[str, list[WirePayload]] = field(default_factory=lambda: defaultdict(list))
    _counts: dict[str, int] = field(default_factory=dict)
    _pending: list[PendingDelivery] = field(default_factory=list)
    _current_step: int = 0
    _rng: random.Random | None = field(default=None, init=False, repr=False)
    _transport_stats: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._transport_stats = {
            "published": 0,
            "delivered": 0,
            "dropped": 0,
            "duplicates_scheduled": 0,
            "pending": 0,
        }
        seed = int(self.transport_config.get("random_seed", 57))
        self._rng = random.Random(seed)
        if self.codec is None:
            self.codec = JsonSimulationCodec()

    def publish(self, message: CITSMessage) -> None:
        self._counts[message.message_type] = self._counts.get(message.message_type, 0) + 1
        self._transport_stats["published"] += 1
        if not bool(self.transport_config.get("enabled", False)):
            self._enqueue(message)
            return

        if self._sample_probability("drop_rate"):
            self._transport_stats["dropped"] += 1
            return

        self._schedule(message)
        if self._sample_probability("duplicate_rate"):
            self._transport_stats["duplicates_scheduled"] += 1
            self._schedule(message)
        self._flush_due()

    def consume(self, destination_id: str) -> list[CITSMessage]:
        payloads = list(self.queues.get(destination_id, []))
        messages = [self._decode_payload(payload) for payload in payloads]
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

    def advance_time(self, step: int | None = None) -> None:
        """Advance simulated transport time and deliver pending messages.

        The broker stays ideal by default. When `message_transport.enabled` is
        true, messages can be delayed, dropped, duplicated, or reordered in a
        deterministic seeded way. Controllers call this at tick boundaries.
        """
        if step is None:
            self._current_step += 1
        else:
            self._current_step = int(step)
        self._flush_due()

    def peek(self, destination_id: str) -> list[CITSMessage]:
        return [self._decode_payload(payload) for payload in self.queues.get(destination_id, [])]

    def count_by_type(self) -> dict[str, int]:
        return dict(self._counts)

    def transport_stats(self) -> dict[str, int]:
        stats = dict(self._transport_stats)
        stats["pending"] = len(self._pending)
        return stats

    def _enqueue(self, message: CITSMessage) -> None:
        self._enqueue_payload(message.destination_id, self._payload_for_transport(message))

    def _enqueue_payload(self, destination_id: str, payload: WirePayload) -> None:
        self.queues[destination_id].append(payload)
        self._transport_stats["delivered"] += 1

    def _schedule(self, message: CITSMessage) -> None:
        latency_steps = max(0, int(self.transport_config.get("latency_steps", 0)))
        jitter_steps = max(0, int(self.transport_config.get("jitter_steps", 0)))
        reorder_window_steps = max(0, int(self.transport_config.get("reorder_window_steps", 0)))
        jitter = self._rng.randint(0, jitter_steps) if self._rng and jitter_steps else 0
        reorder = (
            self._rng.randint(0, reorder_window_steps) if self._rng and reorder_window_steps else 0
        )
        due_step = self._current_step + latency_steps + jitter + reorder
        payload = self._payload_for_transport(message)
        if due_step <= self._current_step:
            self._enqueue_payload(message.destination_id, payload)
            return
        self._pending.append(
            PendingDelivery(
                due_step=due_step,
                destination_id=message.destination_id,
                payload=payload,
            )
        )

    def _flush_due(self) -> None:
        if not self._pending:
            return
        due = [item for item in self._pending if item.due_step <= self._current_step]
        self._pending = [item for item in self._pending if item.due_step > self._current_step]
        for item in sorted(due, key=lambda pending: pending.due_step):
            self._enqueue_payload(item.destination_id, item.payload)

    def _sample_probability(self, key: str) -> bool:
        value = float(self.transport_config.get(key, 0.0))
        if value <= 0.0:
            return False
        if value >= 1.0:
            return True
        return bool(self._rng and self._rng.random() < value)

    def _payload_for_transport(self, message: CITSMessage) -> WirePayload:
        if bool(self.transport_config.get("encode_payloads", False)):
            return self._codec().encode(message)
        return message

    def _decode_payload(self, payload: WirePayload) -> CITSMessage:
        if isinstance(payload, CITSMessage):
            return payload
        return self._codec().decode(payload)

    def _codec(self) -> ProtocolCodec:
        if self.codec is None:
            self.codec = JsonSimulationCodec()
        return self.codec
