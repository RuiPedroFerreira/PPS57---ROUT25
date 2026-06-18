#!/usr/bin/env python3
"""Investigação do issue #68 — os 480 problemas de all-red são falsos-negativos?

Reproduz o estado EXACTO que o verificador viu (materializa Ingolstadt, arranca
SUMO/TraCI em begin, reconcilia os contratos como o controller, re-corre
``_missing_all_red_transitions``) e depois classifica CADA transição falhada
contra a matriz de conflitos AO NÍVEL DO LINK (junction ``<request foes>``), para
distinguir, por transição:

  - same/subset            : o verde-alvo NÃO liberta nenhum movimento novo
                             (mesmo verde mantido/segmentado) -> seguro.
  - nonconflicting_expand  : liberta movimentos novos mas NENHUM é foe de um
                             movimento que estava verde na fase de origem -> seguro.
  - genuine_conflict       : liberta um movimento novo que É foe de um verde da
                             origem -> all-red genuinamente em falta (NÃO relaxar).
  - unknown_unresolved     : sem dados de foe para confirmar (fail-closed honesto).

Não fabrica dados: a matriz vem dos ``<request foes>`` da rede; sem dados ->
unknown. É a confirmação pedida pelo #68 antes de qualquer fix (é safety-gating).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.traci_adapter import TraciSimulationAdapter  # noqa: E402
from pps57_sumo.network_binding import (  # noqa: E402
    _read_junction_tables,
    build_network_binding,
    foe_local_indices,
)
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.signal_control import (  # noqa: E402
    _missing_all_red_transitions,
    _network_tls_profile,
    apply_network_binding,
    build_controller_contracts,
    network_binding_aliases,
    reconcile_contract_with_runtime,
)

# materialize() do runner real, para usar EXACTAMENTE os mesmos ficheiros/sumocfg.
sys.path.insert(0, str(ROOT / "scripts"))
from run_ingolstadt_demo import materialize  # noqa: E402


def _green_links(state: str) -> set[int]:
    return {i for i, ch in enumerate(state) if ch in ("g", "G")}


def main() -> int:
    day, begin = "2023-07-04", "07:00:00"
    sumocfg, net = materialize(day, begin, refresh=False)

    cits = json.loads((ROOT / "configs/cits_ingolstadt_config.json").read_text("utf-8"))
    cits["sumo"]["sumocfg"] = str(sumocfg)
    cits["sumo"]["network"] = str(net)
    # Não precisamos de GTFS/logs para a verificação estrutural; desliga schedule_plan
    # para evitar dependências de ficheiros PT.
    cits["schedule_plan"] = {"enabled": False}
    cits["logging"] = {
        "message_log": str(ROOT / ".tools/ingol_run/out/_a68_msg.jsonl"),
        "summary_report": str(ROOT / ".tools/ingol_run/out/_a68_sum.json"),
        "mapem_snapshot": str(ROOT / ".tools/ingol_run/out/_a68_map.json"),
        "spatem_snapshot": str(ROOT / ".tools/ingol_run/out/_a68_spat.json"),
    }
    cits_path = ROOT / ".tools/ingol_run/_a68_cits.json"
    cits_path.write_text(json.dumps(cits), "utf-8")
    cits_config = load_cits_config(cits_path, root=ROOT)
    tsp_config = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)

    # Matriz de conflitos ao nível do GRUPO (binding) + tabelas de foes ao nível
    # do LINK (slots), da MESMA rede materializada.
    binding = build_network_binding(net)
    aliases = network_binding_aliases(cits_config, tsp_config)
    junction_requests, via_slots = _read_junction_tables(net)
    rep = binding.coverage_report()
    print(f"[binding] cobertura matriz de conflitos (grupo): {rep['coverage_fraction']:.1%} "
          f"({rep['groups_with_authoritative_conflicts']}/{rep['n_signal_groups']} groups)")

    # Contratos offline -> binding -> reconcile com runtime (igual ao controller).
    adapter = TraciSimulationAdapter(cits_config, sumo_binary="sumo", gui=False)
    adapter.start()
    try:
        traci = adapter.traci
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

        # link index -> {(junction, slot)} via getControlledLinks (runtime) + via_slots.
        def link_slots_for(tls_id: str) -> dict[int, set[tuple[str, int]]]:
            out: dict[int, set[tuple[str, int]]] = {}
            try:
                controlled = traci.trafficlight.getControlledLinks(tls_id)
            except Exception:
                return out
            for link_index, tuples in enumerate(controlled):
                slots: set[tuple[str, int]] = set()
                for entry in tuples or []:
                    via = entry[2] if len(entry) > 2 else ""
                    slot = via_slots.get(via)
                    if slot is not None:
                        slots.add(slot)
                out[link_index] = slots
            return out

        def conflict(slots_i: set[tuple[str, int]], slots_j: set[tuple[str, int]]) -> bool:
            for ja, ra in slots_i:
                for jb, rb in slots_j:
                    if ja != jb:
                        continue
                    fa = junction_requests.get(ja, {}).get(ra)
                    fb = junction_requests.get(jb, {}).get(rb)
                    if fa is not None and rb in foe_local_indices(fa):
                        return True
                    if fb is not None and ra in foe_local_indices(fb):
                        return True
            return False

        buckets: Counter[str] = Counter()
        tls_with_genuine: set[str] = set()
        examples: dict[str, list[dict]] = {k: [] for k in
                                            ("genuine_conflict", "unknown_unresolved",
                                             "nonconflicting_expand", "subset_or_same")}
        total_flagged = 0
        tls_count = 0

        for c in contracts:
            states = adapter.read_program_phase_states(c.tls_id)
            durations = adapter.read_program_phase_durations(c.tls_id)
            if not states or durations is None or not c.min_all_red_s or c.min_all_red_s <= 0:
                continue
            tls_count += 1
            flagged = _missing_all_red_transitions(
                states, durations, c.phase_sequence,
                c.service_green_phase_indices, c.min_all_red_s,
            )
            if not flagged:
                continue
            lslots = link_slots_for(c.tls_id)
            for a, b in flagged:
                total_flagged += 1
                gA, gB = _green_links(states[a]), _green_links(states[b])
                newly = gB - gA          # movimentos que ARRANCAM em B
                terminating = gA - gB     # movimentos que TERMINAM (verde em A, não em B)
                if not newly:
                    # B não liberta nenhum movimento novo (mesmo verde mantido/encolhido).
                    bucket = "subset_or_same"
                else:
                    # All-red é exigível só quando um movimento que TERMINA conflitua com
                    # um que ARRANCA. Foes que co-existem verdes em B (protegido/permissivo)
                    # são uma propriedade da própria fase B, não uma clearance da transição.
                    genuine = any(
                        conflict(lslots.get(i, set()), lslots.get(j, set()))
                        for j in newly for i in terminating
                    )
                    if genuine:
                        bucket = "genuine_conflict"
                    else:
                        resolvable = all(lslots.get(L) for L in (newly | terminating))
                        bucket = "nonconflicting_expand" if resolvable else "unknown_unresolved"
                buckets[bucket] += 1
                if bucket == "genuine_conflict":
                    tls_with_genuine.add(c.tls_id)
                if len(examples[bucket]) < 6:
                    examples[bucket].append({
                        "tls": c.tls_id, "from": a, "to": b,
                        "state_from": states[a], "state_to": states[b],
                        "newly_green_links": sorted(newly),
                    })
    finally:
        adapter.close()

    print(f"\n[reproduzido] TLS verificados: {tls_count}; "
          f"transições all-red falhadas: {total_flagged}")
    print("\n==================== CLASSIFICAÇÃO (#68) ====================")
    safe = buckets["subset_or_same"] + buckets["nonconflicting_expand"]
    for k in ("subset_or_same", "nonconflicting_expand", "unknown_unresolved", "genuine_conflict"):
        n = buckets[k]
        pct = (100.0 * n / total_flagged) if total_flagged else 0.0
        print(f"  {k:24s} {n:5d}  ({pct:5.1f}%)")
    print(f"\n  => falso-negativo (seguro):   {safe}/{total_flagged} "
          f"({100.0*safe/total_flagged:.1f}%)" if total_flagged else "")
    print(f"  => genuíno (NÃO relaxar):     {buckets['genuine_conflict']} "
          f"em {len(tls_with_genuine)} TLS")
    print(f"  => indeterminado (fail-close): {buckets['unknown_unresolved']}")

    report = {
        "scenario": {"day": day, "begin": begin, "net": str(net)},
        "binding_coverage": rep["coverage_fraction"],
        "tls_verified": tls_count,
        "total_flagged_all_red": total_flagged,
        "buckets": dict(buckets),
        "tls_with_genuine_conflict": sorted(tls_with_genuine),
        "examples": examples,
    }
    out_path = ROOT / ".tools/ingol_run/out/issue68_all_red_analysis.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), "utf-8")
    print(f"\n[report] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
