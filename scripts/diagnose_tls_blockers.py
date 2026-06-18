#!/usr/bin/env python3
"""Categoriza os problemas de verify_controller_contracts na rede real.

Reproduz o setup do controller (materializa Ingolstadt, arranca SUMO/TraCI,
constrói+liga+reconcilia os contratos) e corre ``verify_controller_contracts``,
agrupando CADA problema por causa. Distingue os blockers de green_extension
(programa não-tempo-fixo/ilegível) dos que só limitam o early_green (matriz de
conflitos, all-red, fase pedonal, ...), para mostrar onde está o teto de cobertura.

Não fabrica nada: lê o que o verificador realmente reporta sobre o programa em
execução. É a evidência por-causa pedida em #69/#67.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.traci_adapter import TraciSimulationAdapter  # noqa: E402
from pps57_sumo.network_binding import build_network_binding  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.signal_control import (  # noqa: E402
    TraciSignalControlAdapter,
    _network_tls_profile,
    apply_network_binding,
    build_controller_contracts,
    network_binding_aliases,
    reconcile_contract_with_runtime,
)

sys.path.insert(0, str(ROOT / "scripts"))
from run_ingolstadt_demo import materialize  # noqa: E402

# Causa por palavra-chave. Ordem importa (primeiro match ganha).
CAUSES = [
    ("program_unreadable", ("ilegível", "estados de fase ilegíveis")),
    ("not_fixed_time", ("tempo fixo", "atuado/adaptativo")),
    ("no_conflict_matrix", ("sem matriz de conflitos",)),          # #69
    ("missing_all_red", ("all-red explícito",)),                    # #70/#69
    ("pedestrian_phase", ("fase pedonal exclusiva",)),              # #64
    ("no_green", ("sem verde",)),                                   # #66
    ("cycle_mismatch", ("ciclo SUMO",)),                            # #65/#67
    ("intergreen_has_green", ("é intergreen mas contém verde",)),
    ("phase_out_of_program", ("fora do programa",)),
    ("yellow_too_short", ("amarelo mínimo",)),
]
GREEN_EXT_BLOCKERS = ("ilegível", "tempo fixo", "atuado/adaptativo", "estados de fase ilegíveis")


def classify(problem: str) -> str:
    body = problem.split(":", 1)[1] if ":" in problem else problem
    for name, markers in CAUSES:
        if any(m in body for m in markers):
            return name
    return "other"


def main() -> int:
    day, begin = "2023-07-04", "07:00:00"
    sumocfg, net = materialize(day, begin, refresh=False)

    cits = json.loads((ROOT / "configs/cits_ingolstadt_config.json").read_text("utf-8"))
    cits["sumo"]["sumocfg"] = str(sumocfg)
    cits["sumo"]["network"] = str(net)
    cits["schedule_plan"] = {"enabled": False}
    cits["logging"] = {k: str(ROOT / f".tools/ingol_run/out/_diag_{k}") for k in
                       ("message_log", "summary_report", "mapem_snapshot", "spatem_snapshot")}
    cits_path = ROOT / ".tools/ingol_run/_diag_cits.json"
    cits_path.write_text(json.dumps(cits), "utf-8")
    cits_config = load_cits_config(cits_path, root=ROOT)
    tsp_config = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)

    binding = build_network_binding(net)
    aliases = network_binding_aliases(cits_config, tsp_config)
    rep = binding.coverage_report()

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
        problems = TraciSignalControlAdapter(adapter).verify_controller_contracts(contracts)
    finally:
        adapter.close()

    by_cause: Counter[str] = Counter()
    tls_by_cause: dict[str, set[str]] = defaultdict(set)
    green_ext_blocked: set[str] = set()
    tls_with_any: set[str] = set()
    for p in problems:
        tls_id = p.split(":", 1)[0].strip()
        cause = classify(p)
        by_cause[cause] += 1
        tls_by_cause[cause].add(tls_id)
        tls_with_any.add(tls_id)
        if any(m in p for m in GREEN_EXT_BLOCKERS):
            green_ext_blocked.add(tls_id)

    n = len(signal_tls)
    print(f"[binding] cobertura matriz de conflitos: {rep['coverage_fraction']:.1%} "
          f"({rep['groups_with_authoritative_conflicts']}/{rep['n_signal_groups']} groups)")
    print(f"\nTLS sinal-controlados: {n}")
    print(f"green_extension atuáveis: {n - len(green_ext_blocked)}/{n} "
          f"({len(green_ext_blocked)} bloqueados por tipo de programa)")
    print(f"early_green atuáveis (zero problemas): {n - len(tls_with_any)}/{n}")
    print("\nproblemas de verify por causa (n_problemas | n_TLS distintos):")
    for cause, _ in CAUSES + [("other", ())]:
        if by_cause.get(cause):
            tag = "[green_ext]" if cause in ("program_unreadable", "not_fixed_time") else "[early_green]"
            print(f"  {cause:22s} {by_cause[cause]:5d} | {len(tls_by_cause[cause]):3d} TLS  {tag}")

    out = ROOT / ".tools/ingol_run/out/tls_blockers_diagnosis.json"
    out.write_text(json.dumps({
        "binding_coverage": rep["coverage_fraction"],
        "n_signal_tls": n,
        "green_extension_actuable": n - len(green_ext_blocked),
        "early_green_actuable": n - len(tls_with_any),
        "problems_by_cause": dict(by_cause),
        "tls_count_by_cause": {k: len(v) for k, v in tls_by_cause.items()},
    }, indent=2), "utf-8")
    print(f"\n[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
