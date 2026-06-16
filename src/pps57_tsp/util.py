#!/usr/bin/env python3
"""Helpers partilhados do pacote TSP (parsing de config e matching de lanes).

Estas funções existiam copiadas em engine/safety/action_planner/compensation/
corridor_arbiter, onde concordavam apenas por disciplina — o mesmo padrão de
drift que já produziu um bug real neste pacote (colisão de prefixo de edge,
M1). Fonte única; os módulos importam com alias `_nome` para manter os
call-sites inalterados.
"""

from __future__ import annotations

from typing import Optional


def positive_float(mapping: dict, key: str, default: float) -> float:
    """Dial de política > 0; ausente/inválido/<=0 -> default."""
    try:
        value = float(mapping.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def non_negative_float(mapping: dict, key: str, default: float) -> float:
    """Dial de política >= 0 (0 pode significar "desligado"); inválido -> default."""
    try:
        value = float(mapping.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def optional_float(value: object) -> Optional[float]:
    """float(value) ou None se ausente/inválido (semântica opcional, fail-closed).

    bool é subclasse de int: um `true/false` por engano não deve virar um
    valor numérico silencioso (true->1.0, false->0.0) — tratar como ausente.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def float_or_default(value: object, default: float) -> float:
    parsed = optional_float(value)
    return parsed if parsed is not None else default


def lane_belongs_to_edge_set(lane_id: Optional[str], edges: set[str]) -> bool:
    """Lane SUMO `<edge>_<index>` pertence a `edges` sse extracted-edge ∈ edges.

    O sufixo numérico obrigatório protege contra colisões de prefixo
    ("I1_I2" vs "I1_I20") sem depender do esquema de nomes das edges.
    """
    if not lane_id or not edges:
        return False
    edge, _, suffix = lane_id.rpartition("_")
    if not edge or not suffix.isdigit():
        return False
    return edge in edges


def controlled_links_match_request(
    links_for_signal: object, lane_id: str, next_edge_id: str
) -> bool:
    """True se algum link controlado liga a lane do pedido à edge seguinte.

    Sem next_edge basta a lane de entrada; com next_edge o link tem de sair
    para essa edge (id exato ou lane `<edge>_<n>`)."""
    if not lane_id or not isinstance(links_for_signal, list):
        return False
    for link in links_for_signal:
        if not isinstance(link, (list, tuple)) or len(link) < 2:
            continue
        incoming_lane = str(link[0])
        outgoing_lane = str(link[1])
        if incoming_lane != lane_id:
            continue
        if not next_edge_id:
            return True
        if outgoing_lane == next_edge_id or outgoing_lane.startswith(f"{next_edge_id}_"):
            return True
    return False
