#!/usr/bin/env python3
"""Verifica o fix do #68 pelo caminho de PRODUÇÃO (matriz offline do NetworkBinding).

Confirma que (a) a matriz de conflitos ao nível do link construída offline pelo
NetworkBinding reproduz a mesma classificação que a análise via TraCI
(`analyze_issue68_all_red.py`), e (b) que ``verify_controller_contracts`` deixa de
reportar os 325 falsos-negativos mas mantém os 65 genuínos + 90 indetermináveis.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_ingolstadt_demo import materialize  # noqa: E402

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.traci_adapter import TraciSimulationAdapter  # noqa: E402
from pps57_sumo.network_binding import build_network_binding  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.signal_control import (  # noqa: E402
    TraciSignalControlAdapter,
    _missing_all_red_transitions,
    _network_tls_profile,
    apply_network_binding,
    build_controller_contracts,
    network_binding_aliases,
    reconcile_contract_with_runtime,
)


def main() -> int:
    sumocfg, net = materialize("2023-07-04", "07:00:00", refresh=False)
    cits = json.loads((ROOT / "configs/cits_ingolstadt_config.json").read_text("utf-8"))
    cits["sumo"]["sumocfg"] = str(sumocfg)
    cits["sumo"]["network"] = str(net)
    cits["schedule_plan"] = {"enabled": False}
    p = ROOT / ".tools/ingol_run/_a68_cits.json"
    p.write_text(json.dumps(cits), "utf-8")
    cits_config = load_cits_config(p, root=ROOT)
    tsp_config = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)

    binding = build_network_binding(net)
    aliases = network_binding_aliases(cits_config, tsp_config)

    adapter = TraciSimulationAdapter(cits_config, sumo_binary="sumo", gui=False)
    adapter.start()
    try:
        signal_tls = {i.tls_id for i in cits_config.signal_controlled_intersections}
        contracts = [
            c for c in build_controller_contracts(cits_config, tsp_config) if c.tls_id in signal_tls
        ]
        contracts = apply_network_binding(contracts, binding, aliases_by_tls=aliases)
        contracts = [
            reconcile_contract_with_runtime(
                c, adapter, _network_tls_profile(cits_config, tsp_config, c.tls_id)
            )
            for c in contracts
        ]

        before = after = 0
        tls_actuable_before: set[str] = set()
        tls_actuable_after: set[str] = set()
        for c in contracts:
            states = adapter.read_program_phase_states(c.tls_id)
            durations = adapter.read_program_phase_durations(c.tls_id)
            if not states or durations is None or not c.min_all_red_s or c.min_all_red_s <= 0:
                continue
            # comportamento LEGADO (sem matriz) vs PÓS-FIX (matriz do contrato).
            legacy = _missing_all_red_transitions(
                states, durations, c.phase_sequence, c.service_green_phase_indices, c.min_all_red_s,
                link_conflicts=None,
            )
            fixed = _missing_all_red_transitions(
                states, durations, c.phase_sequence, c.service_green_phase_indices, c.min_all_red_s,
                link_conflicts=c.link_conflicts, known_conflict_links=c.known_conflict_links,
            )
            before += len(legacy)
            after += len(fixed)
            if not legacy:
                tls_actuable_before.add(c.tls_id)
            if not fixed:
                tls_actuable_after.add(c.tls_id)

        # caminho end-to-end: verify_controller_contracts (conta as strings all-red).
        verifier = TraciSignalControlAdapter(adapter)
        problems = verifier.verify_controller_contracts(contracts)
        all_red_problems = [p for p in problems if "all-red explícito" in p]

        # ramo de regressão: sem matriz autoritativa -> comportamento legado.
        stripped = [
            __import__("dataclasses").replace(c, link_conflicts=None, known_conflict_links=None)
            for c in contracts
        ]
        legacy_problems = [
            p for p in verifier.verify_controller_contracts(stripped) if "all-red explícito" in p
        ]
    finally:
        adapter.close()

    print("==================== VERIFICAÇÃO DO FIX (#68) ====================")
    print(f"all-red flagged  (legado, sem matriz):   {before}")
    print(f"all-red flagged  (pós-fix, com matriz):  {after}")
    print(f"  -> falsos-negativos eliminados:        {before - after}")
    print(f"verify_controller_contracts all-red (legado):  {len(legacy_problems)}")
    print(f"verify_controller_contracts all-red (pós-fix):  {len(all_red_problems)}")
    print(f"TLS sem problema all-red (legado):  {len(tls_actuable_before)}")
    print(f"TLS sem problema all-red (pós-fix): {len(tls_actuable_after)}")

    expected = {"before": 480, "after": 155}
    ok = before == expected["before"] and after == expected["after"] \
        and len(all_red_problems) == after and len(legacy_problems) == before
    print(f"\n{'✅ OK' if ok else '❌ MISMATCH'}: before={before} (esperado {expected['before']}), "
          f"after={after} (esperado {expected['after']}), "
          f"verify pós-fix={len(all_red_problems)}, verify legado={len(legacy_problems)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
