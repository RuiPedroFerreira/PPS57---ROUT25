#!/usr/bin/env python3
"""Conceptual request lifecycle state machine for the SUMO C-ITS profile."""

from __future__ import annotations

from enum import Enum


class PriorityRequestState(str, Enum):
    CREATED = "created"
    PROCESSING = "processing"
    GRANTED = "granted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ALLOWED_TRANSITIONS = {
    PriorityRequestState.CREATED.value: {
        PriorityRequestState.PROCESSING.value,
        PriorityRequestState.REJECTED.value,
        PriorityRequestState.CANCELLED.value,
        PriorityRequestState.EXPIRED.value,
    },
    PriorityRequestState.PROCESSING.value: {
        PriorityRequestState.GRANTED.value,
        PriorityRequestState.REJECTED.value,
        PriorityRequestState.CANCELLED.value,
        PriorityRequestState.EXPIRED.value,
    },
    PriorityRequestState.GRANTED.value: set(),
    PriorityRequestState.REJECTED.value: set(),
    PriorityRequestState.CANCELLED.value: set(),
    PriorityRequestState.EXPIRED.value: set(),
}


def transition_request_state(current: str, target: str) -> str:
    """Validate and apply a conceptual priority-request state transition."""
    current_value = _state_value(current)
    target_value = _state_value(target)
    if target_value == current_value:
        return target_value
    allowed = ALLOWED_TRANSITIONS[current_value]
    if target_value not in allowed:
        raise ValueError(f"invalid priority request transition: {current_value}->{target_value}")
    return target_value


def _state_value(value: str) -> str:
    if value not in ALLOWED_TRANSITIONS:
        raise ValueError(f"unknown priority request state: {value}")
    return value
