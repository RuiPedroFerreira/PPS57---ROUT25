#!/usr/bin/env python3
"""Engine↔network binding with an authoritative conflict matrix.

The TSP engine and Safety Layer reason about *signal groups* and the *conflict
matrix* between them (which movements may not be green together). On hand-built
synthetic nets this matrix is easy; on real imported maps (OSM) it is the thing
that most often goes wrong — and ``signal_control.verify_controller_contracts``
fail-closes a signal group that has movements but an empty conflict matrix
("sem matriz de conflitos"), which disabled TSP actuation on the real Boavista
corridor's joined OSM intersections.

The previous conflict matrix was inferred heuristically from phase-state
disjointness (``network_profile._movement_conflicts``): two movements conflict if
they never share a green phase. That misses permissive movements that *do* share
green, and movements green in every phase — leaving their conflict list empty.

This module binds the engine's signal groups to the concrete SUMO network and
reads the **authoritative** conflict matrix the network already carries: the
junction ``<request foes=...>`` bitmasks SUMO itself uses for right-of-way. It is
correct for joined intersections because it resolves each controlled connection to
its junction-local request index through the connection's ``via`` internal lane:
the request index is the lane's position in the junction's ``intLanes`` attribute.
(The numbers embedded in the lane id are ``<internalEdgeIndex>_<laneIndex>`` — the
edge index is shared by sibling lanes of multi-lane internal edges, so parsing the
id would misindex every junction that has them. The TLS ``linkIndex`` is likewise
unusable: it is global to the program and wrong across a joined TLS.)

It fabricates nothing: where the network genuinely carries no foe data for a
group, the binding records ``conflict_source = "none"`` and the fail-closed gate
correctly stays closed. Safety remains the final gate either way.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# M4: defusedxml em vez do stdlib — net.xml pode vir de fontes externas (OSM).
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

from pps57_sumo.network_profile import (
    ConnectionProfile,
    NetworkProfile,
    load_network_profile,
)


def foe_local_indices(foes: str) -> list[int]:
    """Local request indices set in a SUMO ``foes`` bitmask.

    SUMO writes the bitstring MSB-first: the rightmost character is request
    index 0, so index ``j`` is a foe iff ``foes[len-1-j] == '1'``.
    """
    length = len(foes)
    return [j for j in range(length) if foes[length - 1 - j] == "1"]


@dataclass(frozen=True)
class SignalGroupBinding:
    signal_group_id: str
    tls_id: str
    junction_ids: list[str]
    request_indices: list[tuple[str, int]]  # (junction_id, local request index)
    conflicts_with: list[str]
    conflict_source: str  # "sumo_request_foes" | "none"

    @property
    def conflict_matrix_known(self) -> bool:
        """True when the network gave an authoritative answer (possibly empty)."""
        return self.conflict_source == "sumo_request_foes"


@dataclass(frozen=True)
class BoundTLS:
    tls_id: str
    junction_ids: list[str]
    signal_groups: dict[str, SignalGroupBinding] = field(default_factory=dict)

    def conflicts_for(self, signal_group_id: str) -> list[str]:
        group = self.signal_groups.get(signal_group_id)
        return list(group.conflicts_with) if group else []

    @property
    def groups_with_known_matrix(self) -> int:
        return sum(1 for group in self.signal_groups.values() if group.conflict_matrix_known)


@dataclass(frozen=True)
class NetworkBinding:
    network_file: str
    fingerprint: str
    tls: dict[str, BoundTLS] = field(default_factory=dict)

    def tls_ids(self) -> list[str]:
        return sorted(self.tls)

    def binding_for_tls(self, tls_id: str) -> BoundTLS | None:
        return self.tls.get(tls_id)

    def conflicts_for(self, tls_id: str, signal_group_id: str) -> list[str]:
        bound = self.tls.get(tls_id)
        return bound.conflicts_for(signal_group_id) if bound else []

    def coverage_report(self) -> dict[str, object]:
        """How completely the network's own conflict data binds the signal groups."""
        per_tls: list[dict[str, object]] = []
        total_groups = 0
        total_known = 0
        for tls_id in self.tls_ids():
            bound = self.tls[tls_id]
            n_groups = len(bound.signal_groups)
            n_known = bound.groups_with_known_matrix
            total_groups += n_groups
            total_known += n_known
            per_tls.append(
                {
                    "tls_id": tls_id,
                    "junctions": list(bound.junction_ids),
                    "signal_groups": n_groups,
                    "groups_with_authoritative_conflicts": n_known,
                    "groups_without_foe_data": n_groups - n_known,
                }
            )
        return {
            "network_file": self.network_file,
            "fingerprint": self.fingerprint,
            "n_tls": len(self.tls),
            "n_signal_groups": total_groups,
            "groups_with_authoritative_conflicts": total_known,
            "coverage_fraction": round(total_known / total_groups, 4) if total_groups else 0.0,
            "conflict_source": "sumo_junction_request_foes",
            "per_tls": per_tls,
        }


def _group_for_connection(
    profile: NetworkProfile, tls_id: str, connection: ConnectionProfile
) -> str | None:
    """Signal-group id owning a controlled connection, via its (from,to) movement."""
    tls_profile = profile.tls_profile(tls_id)
    if tls_profile is None:
        return None
    for movement in tls_profile.movements:
        if movement.from_edge == connection.from_edge and movement.to_edge == connection.to_edge:
            return movement.signal_group_id
    return None


def _read_junction_tables(
    net_path: Path,
) -> tuple[dict[str, dict[int, str]], dict[str, tuple[str, int]]]:
    """Read per-junction foes bitmasks and the ``intLanes`` slot map.

    Returns ``(requests, via_slots)``:

    - ``requests[junction_id][request_index]`` -> foes bitstring;
    - ``via_slots[internal_lane_id]`` -> ``(junction_id, request_index)``, where
      the request index is the lane's **position in the junction's ``intLanes``
      attribute** — the definitional mapping SUMO uses for ``<request index=...>``.
      It must not be derived from the lane id's embedded numbers (internal *edge*
      index, shared by sibling lanes of multi-lane internal edges).
    """
    requests: dict[str, dict[int, str]] = {}
    via_slots: dict[str, tuple[str, int]] = {}
    root = ET.fromstring(net_path.read_bytes())
    for junction in root.iter("junction"):
        jid = junction.get("id")
        if not jid:
            continue
        for slot_index, lane_id in enumerate((junction.get("intLanes") or "").split()):
            via_slots[lane_id] = (jid, slot_index)
        by_index: dict[int, str] = {}
        for request in junction.findall("request"):
            idx = request.get("index")
            foes = request.get("foes", "")
            if idx is not None:
                by_index[int(idx)] = foes
        if by_index:
            requests[jid] = by_index
    return requests, via_slots


def build_network_binding(
    net_path: str | Path,
    *,
    additional_files: Iterable[str | Path] | None = None,
) -> NetworkBinding:
    """Bind signal groups to the SUMO net and read the authoritative conflict matrix."""
    path = Path(net_path)
    profile = load_network_profile(path, additional_files=additional_files)
    junction_requests, via_slots = _read_junction_tables(path)

    tls_bindings: dict[str, BoundTLS] = {}
    for tls_id in profile.tls_ids():
        tls_profile = profile.tls_profile(tls_id)
        if tls_profile is None:
            continue
        # (junction, local request index) -> signal_group_id, for this TLS.
        slot_to_group: dict[tuple[str, int], str] = {}
        # Seed every signal group up front so groups whose connections carry no
        # resolvable via are still *recorded* (conflict_source="none") instead of
        # silently dropped — keeping coverage_report honest.
        group_slots: dict[str, list[tuple[str, int]]] = {
            movement.signal_group_id: [] for movement in tls_profile.movements
        }
        for connection in tls_profile.connections:
            slot = via_slots.get(connection.via)
            if slot is None:
                continue
            group_id = _group_for_connection(profile, tls_id, connection)
            if group_id is None:
                continue
            slot_to_group[slot] = group_id
            group_slots.setdefault(group_id, []).append(slot)

        # Resolve each group's foes through the junction request bitmasks.
        groups: dict[str, SignalGroupBinding] = {}
        for group_id, slots in group_slots.items():
            conflicts: set[str] = set()
            had_foe_data = False
            junctions: set[str] = set()
            for junction_id, req_idx in slots:
                junctions.add(junction_id)
                foes_by_index = junction_requests.get(junction_id)
                if not foes_by_index or req_idx not in foes_by_index:
                    continue
                had_foe_data = True
                for foe_idx in foe_local_indices(foes_by_index[req_idx]):
                    foe_group = slot_to_group.get((junction_id, foe_idx))
                    if foe_group and foe_group != group_id:
                        conflicts.add(foe_group)
            groups[group_id] = SignalGroupBinding(
                signal_group_id=group_id,
                tls_id=tls_id,
                junction_ids=sorted(junctions),
                request_indices=sorted(set(slots)),
                conflicts_with=sorted(conflicts),
                conflict_source="sumo_request_foes" if had_foe_data else "none",
            )
        tls_junctions = sorted({jid for slots in group_slots.values() for jid, _ in slots})
        tls_bindings[tls_id] = BoundTLS(
            tls_id=tls_id, junction_ids=tls_junctions, signal_groups=groups
        )

    return NetworkBinding(
        network_file=str(path),
        fingerprint=profile.fingerprint,
        tls=tls_bindings,
    )
