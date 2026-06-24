#!/usr/bin/env python3
"""NetworkBinding evidence: authoritative conflict matrix removes the fail-close.

On joined intersections the contract-verification gate's "sem matriz de conflitos"
check trips: signal groups with movements but an empty conflict matrix. That matrix
was inferred heuristically from phase-state disjointness, which cannot see permissive
movements that share green.

This script demonstrates the fix end-to-end through the *real* contract-building path:

  1. build the controller contracts exactly as the demo does (auto network discovery
     + generated contracts), and count the signal groups that trip the fail-close
     predicate from ``verify_controller_contracts``;
  2. build the :class:`NetworkBinding` (authoritative conflicts from SUMO junction
     ``<request foes>``), apply it to the contracts, and re-count.

It fabricates nothing: every conflict comes from the network's own foe data, and a
group the network genuinely leaves without foe data still fail-closes. Safety
remains the final gate. Evidence is written to
``reports/validation/networkbinding_check.json``.

Pré-requisito: a net do corredor sintético em ``sumo/network/corredor.net.xml``
(corre ``make build``), ou aponta ``--net`` para outra net.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for entry in (str(SRC), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from _evidence_common import auto_discovery_cits_config, auto_tsp_config  # noqa: E402

from pps57_sumo.network_binding import build_network_binding  # noqa: E402
from pps57_tsp.signal_control import (  # noqa: E402
    ControllerContract,
    apply_network_binding,
    build_controller_contracts,
    network_binding_aliases,
    signal_group_lacks_conflict_matrix,
)

DEFAULT_NET = ROOT / "sumo" / "network" / "corredor.net.xml"


def _fail_close_groups(contracts) -> list[dict]:
    """Groups that trip verify_controller_contracts' 'sem matriz de conflitos' check.

    The predicate is imported from signal_control (the verifier's own condition),
    so this count cannot drift from what the Safety Layer actually enforces.
    """
    tripped: list[dict] = []
    for contract in contracts:
        for group in contract.signal_groups.values():
            if signal_group_lacks_conflict_matrix(group):
                tripped.append(
                    {"tls_id": contract.tls_id, "signal_group_id": group.signal_group_id}
                )
    return tripped


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "reports" / "validation" / "networkbinding_check.json",
    )
    args = parser.parse_args()
    if not args.net.exists():
        raise SystemExit(
            f"Missing {args.net}. Gera a net do corredor com `make build` "
            "(ou aponta --net para outra net)."
        )

    cits = auto_discovery_cits_config(args.net)
    tsp = auto_tsp_config(ROOT)
    contracts: list[ControllerContract] = build_controller_contracts(cits, tsp)

    binding = build_network_binding(args.net)
    bound_contracts = apply_network_binding(
        contracts, binding, aliases_by_tls=network_binding_aliases(cits, tsp)
    )

    before = _fail_close_groups(contracts)
    after = _fail_close_groups(bound_contracts)
    coverage = binding.coverage_report()

    total_groups = sum(len(c.signal_groups) for c in contracts)
    report = {
        "validation_phase": "network_binding_authoritative_conflict_matrix",
        # Path relativo ao repo: evidência committed não deve embeber o home do utilizador.
        "network": os.path.relpath(args.net, ROOT),
        "fingerprint": binding.fingerprint,
        "summary": {
            "tls": len(contracts),
            "signal_groups": total_groups,
            "fail_close_groups_before_binding": len(before),
            "fail_close_groups_after_binding": len(after),
            "binding_conflict_coverage_fraction": coverage["coverage_fraction"],
            "conflict_source": coverage["conflict_source"],
        },
        "honest_notes": [
            "Conflicts come only from the SUMO net's own <request foes> data; nothing is fabricated.",
            "A group the network leaves without foe data still fail-closes (conflict_source='none').",
            "The binding never grants a permission — it supplies conflict info; the Safety Layer stays the final gate.",
            "Predicate counted is signal_control.signal_group_lacks_conflict_matrix — the exact "
            "'sem matriz de conflitos' check verify_controller_contracts applies.",
        ],
        "fail_close_groups_before_sample": before[:20],
        "fail_close_groups_after": after,
        # B51: before==0 means the network was ALREADY fully covered — a correct
        # state, reported as "already_clean" (a success), not the ambiguous "noop"
        # that read like a failed/inconclusive check.
        "verdict": "pass"
        if (len(before) > 0 and len(after) == 0)
        else ("already_clean" if len(before) == 0 else "review"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"NetworkBinding check — {len(contracts)} TLS, {total_groups} signal groups")
    print(
        f"  fail-close groups: before={len(before)}  after={len(after)}  "
        f"(binding coverage {coverage['coverage_fraction'] * 100:.1f}%)"
    )
    print(f"  verdict: {report['verdict']}  -> {args.out}")
    if report["verdict"] == "review":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
