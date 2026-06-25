#!/usr/bin/env python3
"""Caracteriza POR CAUSA a não-cobertura da matriz de conflitos (#69).

Estático (sem SUMO): para cada conexão controlada por TLS, classifica porque é
que o seu ``via`` resolve (ou não) para um slot de request com foes:

  - resolved              : via está nos intLanes de uma junction com <request>.
  - via_empty             : conexão sem via (sem internal lane).
  - via_not_in_request_jn : via aponta para um internal lane que NÃO está nos
                            intLanes de nenhuma junction com tabela <request>
                            (tipicamente internal lanes de internal junctions).

Mostra também quantos signal groups ficam sem QUALQUER conexão resolvida (->
conflict_source="none" -> fail-closed). Não fabrica nada; só diz onde está a perda.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.network_binding import _read_junction_tables  # noqa: E402
from pps57_sumo.network_profile import load_network_profile  # noqa: E402


def main() -> int:
    net = ROOT / "sumo/network/corredor.net.xml"
    if not net.exists():
        raise SystemExit(f"net não encontrada: {net} (corre `make build` para a gerar)")
    profile = load_network_profile(net)
    _requests, via_slots = _read_junction_tables(net)

    # Lanes que pertencem a ALGUMA intLanes de junction (com ou sem request).
    root = ET.fromstring(net.read_bytes())
    intlanes_any: set[str] = set()
    for jn in root.iter("junction"):
        for lane in (jn.get("intLanes") or "").split():
            intlanes_any.add(lane)

    conn_causes: Counter[str] = Counter()
    groups_total = 0
    groups_no_resolved = 0
    for tls_id in profile.tls_ids():
        tp = profile.tls_profile(tls_id)
        if tp is None:
            continue
        resolved_by_group: dict[str, bool] = {m.signal_group_id: False for m in tp.movements}
        # mapear conexão -> group (mesma lógica do binding)
        # B49: keep-first + warn when an edge pair is shared by movements in different
        # signal groups, instead of the last-wins dict silently dropping one.
        mv_by_edges: dict[tuple[str, str], str] = {}
        for m in tp.movements:
            key = (m.from_edge, m.to_edge)
            existing = mv_by_edges.get(key)
            if existing is not None and existing != m.signal_group_id:
                print(
                    f"[warn] B49: edge pair {key} mapeia a múltiplos signal groups "
                    f"({existing} e {m.signal_group_id}); usado o primeiro.",
                    file=sys.stderr,
                )
                continue
            mv_by_edges.setdefault(key, m.signal_group_id)
        for conn in tp.connections:
            via = conn.via
            if not via:
                conn_causes["via_empty"] += 1
                cause = "via_empty"
            elif via in via_slots:
                conn_causes["resolved"] += 1
                cause = "resolved"
            elif via in intlanes_any:
                conn_causes["via_in_internal_junction_only"] += 1
                cause = "unresolved"
            else:
                conn_causes["via_not_in_any_intlanes"] += 1
                cause = "unresolved"
            gid = mv_by_edges.get((conn.from_edge, conn.to_edge))
            if gid is not None and cause == "resolved":
                resolved_by_group[gid] = True
        for _gid, ok in resolved_by_group.items():
            groups_total += 1
            if not ok:
                groups_no_resolved += 1

    total_conn = sum(conn_causes.values())
    print(f"net: {net.name}")
    print(f"\nconexões controladas: {total_conn}")
    for cause in (
        "resolved",
        "via_empty",
        "via_in_internal_junction_only",
        "via_not_in_any_intlanes",
    ):
        n = conn_causes.get(cause, 0)
        pct = 100.0 * n / total_conn if total_conn else 0.0
        print(f"  {cause:32s} {n:6d}  ({pct:5.1f}%)")
    # B50: guard against a network with no signal groups (ZeroDivisionError).
    no_resolved_pct = 100.0 * groups_no_resolved / groups_total if groups_total else 0.0
    coverage_pct = (
        100.0 * (groups_total - groups_no_resolved) / groups_total if groups_total else 0.0
    )
    print(
        f"\nsignal groups: {groups_total}; "
        f"sem QUALQUER conexão resolvida (fail-closed): {groups_no_resolved} "
        f"({no_resolved_pct:.1f}%)"
    )
    print(f"cobertura esperada: {coverage_pct:.1f}%")

    out = ROOT / "reports/validation/binding_coverage_causes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "net": net.name,
                "controlled_connections": total_conn,
                "connection_causes": dict(conn_causes),
                "signal_groups": groups_total,
                "groups_without_resolved_connection": groups_no_resolved,
            },
            indent=2,
        ),
        "utf-8",
    )
    print(f"\n[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
