"""Post-build step that injects coordination + phase + clearance overrides into a SUMO net.xml.

`netconvert` does not expose per-junction offsets as CLI flags, emits its
default green split (~equal between main and cross), and does not emit
clearance (all-red) intervals between conflicting phases. Once the network is
built, this module reads a `tlsOffsetOverrides` XML produced by
``generate_plain_corridor.build_tls_offsets`` and:

  * rewrites the ``offset`` attribute on each matching ``<tlLogic>`` element
    (legacy behaviour — name preserved for back-compat);
  * if the override contains ``<phase role="main_green|main_yellow|
    cross_green|cross_yellow">`` children, classifies each generated phase by
    inspecting the network's ``<connection>`` elements and the phase ``state``
    string, then rewrites the matching ``<phase duration="...">`` attribute.
    State strings stay intact so the link count and the structural validity
    of the program are preserved;
  * if the override contains ``<phase role="all_red_main_to_cross">`` or
    ``<phase role="all_red_cross_to_main">``, inserts a new all-red ``<phase>``
    after the corresponding yellow with ``state`` = all ``r`` matching the
    link-vector length. The function is idempotent: any pre-existing all-red
    phase is removed before insertion so re-running the script after another
    rebuild yields the same final plan.
  * if the override contains ``<phase role="pedestrian">``, inserts a new
    exclusive pedestrian ``<phase>`` after ``all_red_cross_to_main`` (or after
    ``cross_yellow`` if no all-red is configured). The state is ``r`` on every
    vehicle link and ``G`` on the indices that netconvert assigned to
    pedestrian ``<crossing>`` connections (identified by having ``G`` in both
    the main_green and cross_green source phases). This turns concurrent
    crossings into a Barnes Dance, eliminating the turn-on-crossing conflict.
    Idempotent: any pre-existing exclusive-ped phase is stripped before
    insertion.

Without the offset step every signal starts at t=0 and the corridor acts like
seven independent timers. Without the phase-duration step every TLS keeps the
netconvert default split (~42s/42s), which is too generous to the cross-street
for an arterial. Without the all-red step the signal jumps directly from
yellow to the perpendicular green, eliminating the safety clearance interval
that PT engineering practice (and the HCM intergreen calculation) requires.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

# M4: net.xml/overrides podem vir de fontes externas (OSM) -> endurece o parsing
# de input não-confiável contra XXE/expansão de entidades. A construção de
# elementos (`ET.Element`/`ET.SubElement`) mantém-se do stdlib (defusedxml não a
# expõe e não processa input externo).
try:
    from defusedxml.ElementTree import parse as _safe_parse
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    _safe_parse = ET.parse


_MAIN_EDGE_RE = re.compile(r"^I\d+_I\d+$")
_ALL_RED_INSERT_ROLES = ("all_red_main_to_cross", "all_red_cross_to_main")
_PRECEDING_YELLOW_FOR_ALL_RED = {
    "all_red_main_to_cross": "main_yellow",
    "all_red_cross_to_main": "cross_yellow",
}
_PED_INSERT_ROLE = "pedestrian"


def apply_tls_offsets(net_path: Path, overrides_path: Path) -> int:
    """Apply offset + phase-duration + all-red overrides to a generated SUMO .net.xml in-place.

    Returns the number of ``<tlLogic>`` elements actually modified. Missing
    overrides file is a no-op (returns 0). Missing net file raises.
    """
    if not Path(net_path).exists():
        raise FileNotFoundError(f"net file not found: {net_path}")
    if not Path(overrides_path).exists():
        return 0

    overrides_tree = _safe_parse(str(overrides_path))
    overrides_by_tls: dict[str, dict] = {}
    for elem in overrides_tree.getroot().findall("tls"):
        tls_id = elem.attrib.get("id")
        if not tls_id:
            continue
        entry: dict = {}
        if "offset_s" in elem.attrib:
            entry["offset_s"] = float(elem.attrib["offset_s"])
        phase_overrides = {
            str(phase.attrib["role"]): float(phase.attrib["duration_s"])
            for phase in elem.findall("phase")
            if "role" in phase.attrib and "duration_s" in phase.attrib
        }
        if phase_overrides:
            entry["phases"] = phase_overrides
        if entry:
            overrides_by_tls[tls_id] = entry
    if not overrides_by_tls:
        return 0

    net_tree = _safe_parse(str(net_path))
    net_root = net_tree.getroot()
    connections_by_tls = _index_connections_by_tls(net_root)

    modified = 0
    for tl_logic in net_root.findall("tlLogic"):
        tls_id = tl_logic.attrib.get("id")
        if tls_id not in overrides_by_tls:
            continue
        override = overrides_by_tls[tls_id]
        if "offset_s" in override:
            tl_logic.attrib["offset"] = f"{override['offset_s']:.1f}"
        if "phases" in override:
            _apply_phase_program(
                tl_logic,
                connections_by_tls.get(tls_id, {}),
                override["phases"],
                tls_id=tls_id,
            )
        modified += 1
    if modified:
        net_tree.write(str(net_path), encoding="utf-8", xml_declaration=True)
    return modified


def _index_connections_by_tls(net_root: ET.Element) -> dict[str, dict[int, tuple[str, str]]]:
    out: dict[str, dict[int, tuple[str, str]]] = {}
    for conn in net_root.findall("connection"):
        tls_id = conn.attrib.get("tl")
        if not tls_id:
            continue
        idx = conn.attrib.get("linkIndex")
        if idx is None:
            continue
        out.setdefault(tls_id, {})[int(idx)] = (
            str(conn.attrib.get("from", "")),
            str(conn.attrib.get("to", "")),
        )
    return out


def _apply_phase_program(
    tl_logic: ET.Element,
    connections: dict[int, tuple[str, str]],
    role_durations: dict[str, float],
    *,
    tls_id: str,
) -> None:
    main_links, cross_links = _classify_links(connections)
    if not main_links or not cross_links:
        raise ValueError(
            f"TLS {tls_id}: cannot identify main/cross links from connections; "
            f"got main={sorted(main_links)} cross={sorted(cross_links)}."
        )

    will_insert_all_red = any(role in role_durations for role in _ALL_RED_INSERT_ROLES)
    will_insert_ped = _PED_INSERT_ROLE in role_durations
    if will_insert_all_red:
        _strip_all_red_phases(tl_logic)
    ped_indices: set[int] = set()
    if will_insert_ped:
        ped_indices = _ped_link_indices(connections)
        if not ped_indices:
            raise ValueError(
                f"TLS {tls_id}: cannot insert exclusive pedestrian phase because no "
                f"pedestrian/crossing linkIndex slots were found on this TLS's connections. "
                f"Was the network built with --crossings.guess and --walkingareas?"
            )
        _strip_ped_phases(tl_logic, main_links, cross_links)
        # An exclusive Barnes Dance replaces concurrent pedestrian crossings.
        # netconvert always emits a "flashing don't walk" sub-phase after each
        # vehicle green to clear those concurrent crossings; once we strip them
        # we must also turn the crossing indices to red in the surviving
        # vehicle green phases (otherwise peds keep getting concurrent G in
        # main_green / cross_green, and the FDW would still be needed). Both
        # operations are guarded behind will_insert_ped so non-ped TLS keep the
        # netconvert default plan intact. Vehicle turns (one main edge, one
        # cross edge) are NOT in ped_indices and are left untouched.
        _strip_concurrent_ped_clearance_phases(
            tl_logic, main_links, cross_links, ped_indices, tls_id=tls_id
        )
        _disable_concurrent_crossings(tl_logic, ped_indices)

    role_to_phase: dict[str, ET.Element] = {}
    for phase in tl_logic.findall("phase"):
        role = _classify_phase_role(phase.attrib.get("state", ""), main_links, cross_links)
        if role and role not in role_to_phase:
            role_to_phase[role] = phase

    for role, duration in role_durations.items():
        if role in _ALL_RED_INSERT_ROLES or role == _PED_INSERT_ROLE:
            continue
        phase = role_to_phase.get(role)
        if phase is None:
            raise ValueError(
                f"TLS {tls_id}: override references role '{role}' but no matching phase was found "
                f"in the generated tlLogic (available: {sorted(role_to_phase)})."
            )
        phase.attrib["duration"] = f"{duration:.1f}"
        # Keep actuated minDur/maxDur consistent with the configured plan so a
        # `dur=51 > maxDur=50` mismatch never silently caps the nominal green.
        # Coordinated-actuated policy: phase can shorten down to minDur (5s by
        # default) under light traffic, but never extends beyond the static
        # plan duration — which is what preserves corridor coordination.
        if "maxDur" in phase.attrib or "minDur" in phase.attrib:
            phase.attrib["maxDur"] = f"{duration:.1f}"
            current_min = phase.attrib.get("minDur")
            try:
                current_min_v = float(current_min) if current_min is not None else 5.0
            except ValueError:
                current_min_v = 5.0
            phase.attrib["minDur"] = f"{min(current_min_v, duration):.1f}"

    if not (will_insert_all_red or will_insert_ped):
        return

    state_length = _state_length(role_to_phase)
    all_red_state = "r" * state_length
    # Insert in reverse cycle order so the index of the earlier-cycle yellow
    # is not shifted by the later insertion.
    for role in ("all_red_cross_to_main", "all_red_main_to_cross"):
        if role not in role_durations:
            continue
        preceding_role = _PRECEDING_YELLOW_FOR_ALL_RED[role]
        anchor = role_to_phase.get(preceding_role)
        if anchor is None:
            raise ValueError(
                f"TLS {tls_id}: cannot insert '{role}' because preceding '{preceding_role}' phase "
                f"is missing in the generated tlLogic."
            )
        children = list(tl_logic)
        anchor_idx = children.index(anchor)
        new_phase = ET.Element(
            "phase",
            {"duration": f"{role_durations[role]:.1f}", "state": all_red_state},
        )
        tl_logic.insert(anchor_idx + 1, new_phase)

    if not will_insert_ped:
        return

    cross_yellow = role_to_phase.get("cross_yellow")
    if cross_yellow is None:
        raise ValueError(
            f"TLS {tls_id}: cannot insert exclusive pedestrian phase because no 'cross_yellow' "
            f"phase was found (available: {sorted(role_to_phase)})."
        )
    ped_state = _build_ped_state(ped_indices, state_length)
    # Anchor after cross_yellow, then skip past any all-red that was just
    # inserted by the loop above so the ped phase lands at end-of-cycle.
    children = list(tl_logic)
    anchor_idx = children.index(cross_yellow)
    while anchor_idx + 1 < len(children) and _is_all_red_phase(children[anchor_idx + 1]):
        anchor_idx += 1
    ped_phase = ET.Element(
        "phase",
        {"duration": f"{role_durations[_PED_INSERT_ROLE]:.1f}", "state": ped_state},
    )
    tl_logic.insert(anchor_idx + 1, ped_phase)


def _strip_all_red_phases(tl_logic: ET.Element) -> None:
    """Remove pre-existing all-red phases so re-runs do not duplicate them."""
    for child in list(tl_logic):
        if child.tag != "phase":
            continue
        if _is_all_red_phase(child):
            tl_logic.remove(child)


def _is_all_red_phase(phase: ET.Element) -> bool:
    state = phase.attrib.get("state", "")
    return bool(state) and all(c == "r" for c in state)


def _strip_concurrent_ped_clearance_phases(
    tl_logic: ET.Element,
    main_links: Iterable[int],
    cross_links: Iterable[int],
    ped_indices: Iterable[int],
    *,
    tls_id: str,
) -> None:
    """Remove the netconvert flashing-don't-walk phases that clear concurrent crossings.

    With ``--crossings.guess`` netconvert generates 8 phases per cycle: each
    vehicle-green phase is followed by a near-duplicate that only differs in
    the crossing indices (those flip to ``r`` while vehicles still have
    green) so pedestrians mid-crossing have time to finish. Two safeguards
    keep the strip from over-reaching if the netconvert output deviates:

    1. The candidate phase's vehicle state (state with ped indices masked
       out) must equal that of the earlier same-role phase — i.e., the
       vehicle program is *literally* unchanged.
    2. Every difference between the two states must be on a ped index, and
       must be of the form ``G/g → r`` (a clearance), not ``r → G`` (a
       promotion). A phase that brightens peds is not a clearance and
       deserves to stay.

    After the strip the function asserts that exactly one ``main_green`` and
    one ``cross_green`` phase remain — if either count is off, the
    netconvert plan no longer matches the assumed shape and we fail loud
    instead of silently corrupting the cycle.
    """
    ped_set = {int(i) for i in ped_indices}
    first_by_role: dict[str, ET.Element] = {}
    for child in list(tl_logic):
        if child.tag != "phase":
            continue
        role = _classify_phase_role(child.attrib.get("state", ""), main_links, cross_links)
        if role not in ("main_green", "cross_green"):
            continue
        if role not in first_by_role:
            first_by_role[role] = child
            continue
        first = first_by_role[role]
        if _is_ped_clearance_of(
            child.attrib.get("state", ""),
            first.attrib.get("state", ""),
            ped_set,
        ):
            tl_logic.remove(child)

    role_counts = {"main_green": 0, "cross_green": 0}
    for phase in tl_logic.findall("phase"):
        role = _classify_phase_role(
            phase.attrib.get("state", ""), main_links, cross_links
        )
        if role in role_counts:
            role_counts[role] += 1
    for role, count in role_counts.items():
        if count != 1:
            raise ValueError(
                f"TLS {tls_id}: after stripping ped-clearance phases expected exactly "
                f"one '{role}' phase, got {count}. The netconvert output may have changed; "
                f"inspect the generated tlLogic before forcing an exclusive ped phase."
            )


def _is_ped_clearance_of(
    candidate_state: str,
    original_state: str,
    ped_set: set[int],
) -> bool:
    """True iff ``candidate_state`` is the flashing-don't-walk sub-phase of ``original_state``.

    Required pattern: same length, identical on every non-ped index, and on
    each ped index either unchanged or transitioning ``G/g → r`` (clearance).
    Any vehicle-side delta or a ``r → G`` ped delta disqualifies the
    candidate as a clearance.
    """
    if len(candidate_state) != len(original_state):
        return False
    for i, (c, o) in enumerate(zip(candidate_state, original_state)):
        if i in ped_set:
            if c == o:
                continue
            if o in ("G", "g") and c == "r":
                continue
            return False
        elif c != o:
            return False
    return True


def _disable_concurrent_crossings(
    tl_logic: ET.Element,
    ped_indices: Iterable[int],
) -> None:
    """Turn the supplied pedestrian indices to red inside every phase.

    Without this step the inserted exclusive ped phase would coexist with
    netconvert's concurrent crossings — peds would get green twice per cycle
    (once with each vehicle phase, once exclusive). This function enforces a
    pure Barnes Dance by zeroing the crossing slots across the whole cycle;
    the exclusive ped phase (inserted later) then reopens them. Vehicle
    indices (main, cross, and turns) are untouched.
    """
    ped_set = {int(i) for i in ped_indices}
    if not ped_set:
        return
    for child in tl_logic.findall("phase"):
        state = list(child.attrib.get("state", ""))
        changed = False
        for i in ped_set:
            if 0 <= i < len(state) and state[i] in ("G", "g"):
                state[i] = "r"
                changed = True
        if changed:
            child.attrib["state"] = "".join(state)


def _strip_ped_phases(
    tl_logic: ET.Element,
    main_links: Iterable[int],
    cross_links: Iterable[int],
) -> None:
    """Remove pre-existing exclusive-pedestrian phases so re-runs do not duplicate them.

    Exclusive ped phases are those classified as ``pedestrian`` by
    :func:`_classify_phase_role` — vehicle indices all red, crossing indices
    green. The netconvert-generated concurrent-crossing phases keep their G's
    on the parent vehicle phase (main_green / cross_green) and are not touched.
    """
    main_set = set(main_links)
    cross_set = set(cross_links)
    for child in list(tl_logic):
        if child.tag != "phase":
            continue
        role = _classify_phase_role(child.attrib.get("state", ""), main_set, cross_set)
        if role == "pedestrian":
            tl_logic.remove(child)


def _ped_link_indices(connections: dict[int, tuple[str, str]]) -> set[int]:
    """linkIndex slots whose ``<connection>`` runs through an internal junction edge.

    netconvert uses ``:I<n>_w<k>`` for walking-area internal edges and
    ``:I<n>_c<k>`` for crossing internal edges; vehicular connections always
    run between real (non-prefixed) edges. So every slot whose either endpoint
    starts with ``:`` is a pedestrian movement.
    """
    return {
        idx
        for idx, (from_edge, to_edge) in connections.items()
        if from_edge.startswith(":") or to_edge.startswith(":")
    }


def _build_ped_state(ped_indices: Iterable[int], state_length: int) -> str:
    """Construct the state string for the exclusive ped phase: ``G`` on every
    pedestrian slot, ``r`` everywhere else."""
    chars = ["r"] * state_length
    for idx in ped_indices:
        if 0 <= int(idx) < state_length:
            chars[int(idx)] = "G"
    return "".join(chars)


def _state_length(role_to_phase: dict[str, ET.Element]) -> int:
    """Pick the longest state string in the classified phases as the canonical link-vector length."""
    lengths = {len(phase.attrib.get("state", "")) for phase in role_to_phase.values()}
    if not lengths:
        raise ValueError("Cannot determine state vector length from empty role mapping.")
    return max(lengths)


def _classify_links(connections: dict[int, tuple[str, str]]) -> tuple[set[int], set[int]]:
    """Split link indices into corridor-main and cross-street based on edge naming.

    Main corridor edges follow ``I<n>_I<m>`` or contain the terminal labels
    ``CITY_EAST`` / ``ATLANTIC_WEST``. Cross-street edges start with ``N_`` /
    ``S_`` (cross approaches) or contain ``_N_`` / ``_S_`` (cross exits). A
    link is "main through" when both endpoints are corridor edges; "cross
    through" when both endpoints are cross edges. Pedestrian links (added by
    ``--crossings.guess`` / ``--walkingareas``) DO get their own
    ``<connection>`` elements with ``tl=`` and ``linkIndex=`` attributes — their
    endpoints are internal junction edges prefixed with ``:`` (e.g.
    ``:I1_w0`` → ``:I1_c3``). This classifier ignores them because neither
    endpoint matches the main- or cross-edge naming, so they end up in
    neither set. See :func:`_ped_link_indices` for the dedicated pedestrian
    identifier used by the exclusive ped-phase insertion path.
    """
    main_links: set[int] = set()
    cross_links: set[int] = set()
    for link_idx, (from_edge, to_edge) in connections.items():
        if _is_main_edge(from_edge) and _is_main_edge(to_edge):
            main_links.add(link_idx)
        elif _is_cross_edge(from_edge) and _is_cross_edge(to_edge):
            cross_links.add(link_idx)
    return main_links, cross_links


def _is_main_edge(edge_id: str) -> bool:
    if not edge_id:
        return False
    if _MAIN_EDGE_RE.match(edge_id):
        return True
    return "CITY_EAST" in edge_id or "ATLANTIC_WEST" in edge_id


def _is_cross_edge(edge_id: str) -> bool:
    if not edge_id:
        return False
    if edge_id.startswith("N_") or edge_id.startswith("S_"):
        return True
    return "_N_" in edge_id or "_S_" in edge_id


def _classify_phase_role(
    state: str,
    main_links: Iterable[int],
    cross_links: Iterable[int],
) -> str | None:
    """Classify a phase as ``main_green`` / ``main_yellow`` / ``cross_green`` /
    ``cross_yellow`` / ``all_red`` / ``pedestrian`` / ``None``.

    ``pedestrian`` is reported when the only ``G`` characters in the state fall
    outside both main and cross link sets (e.g., on ``<crossing>`` indices).
    The caller can use this signal to skip duration overrides on ped-only
    phases without raising.
    """
    if state and all(c == "r" for c in state):
        return "all_red"
    main_g = _count_signal(state, main_links, ("G", "g"))
    main_y = _count_signal(state, main_links, ("y", "Y"))
    cross_g = _count_signal(state, cross_links, ("G", "g"))
    cross_y = _count_signal(state, cross_links, ("y", "Y"))
    if main_g > 0 and main_y == 0 and cross_g == 0 and cross_y == 0:
        return "main_green"
    if main_y > 0 and main_g == 0 and cross_g == 0 and cross_y == 0:
        return "main_yellow"
    if cross_g > 0 and cross_y == 0 and main_g == 0 and main_y == 0:
        return "cross_green"
    if cross_y > 0 and cross_g == 0 and main_g == 0 and main_y == 0:
        return "cross_yellow"
    # No main/cross signalling — must be a ped-only phase if there are any G's.
    if main_g == 0 and main_y == 0 and cross_g == 0 and cross_y == 0 and any(c in ("G", "g") for c in state):
        return "pedestrian"
    return None


def _count_signal(state: str, link_indices: Iterable[int], chars: tuple[str, ...]) -> int:
    return sum(1 for i in link_indices if 0 <= i < len(state) and state[i] in chars)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--overrides", required=True, type=Path)
    args = parser.parse_args()
    n = apply_tls_offsets(args.net, args.overrides)
    print(f"applied {n} tls overrides")


if __name__ == "__main__":
    main()
