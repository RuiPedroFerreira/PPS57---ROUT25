#!/usr/bin/env python3
"""Static validation for the PPS57 SUMO realistic scenario.

This does not replace validation with SUMO. It checks the file structure, XML
well-formedness, and the safety-critical numeric invariants of the TSP/C-ITS
configuration files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict
# M4: defusedxml em vez do stdlib para validação de XML do projeto.
from defusedxml import ElementTree as ET  # type: ignore[import-untyped]

REQUIRED_FILES = [
    "configs/corridor_config.json",
    "configs/corridor_config_porto_boavista_realistic.json",
    "configs/scenarios.yaml",
    "configs/cits_config.json",
    "configs/tsp_config.json",
    "sumo/plain/corredor.nod.xml",
    "sumo/plain/corredor.edg.xml",
    "sumo/routes/routes.rou.xml",
    "sumo/additional/bus_stops.add.xml",
    "sumo/additional/detectors.add.xml",
    "sumo/corredor.sumocfg",
    "docs/CENARIO_REALISTA_BOAVISTA.md",
]

XML_FILES = [
    "sumo/plain/corredor.nod.xml",
    "sumo/plain/corredor.edg.xml",
    "sumo/routes/routes.rou.xml",
    "sumo/additional/bus_stops.add.xml",
    "sumo/additional/detectors.add.xml",
    "sumo/corredor.sumocfg",
]


def validate_routes_sorted(routes_path: Path) -> None:
    """Falha cedo se o ficheiro de rotas estiver fora de ordem temporal.

    Com `<ignore-route-errors value="true"/>` no sumocfg, o SUMO degrada o erro
    "should be sorted" para warning e **descarta** silenciosamente os veículos
    fora de ordem (incluindo autocarros). Esta verificação estática evita a
    regressão silenciosa.
    """
    tree = ET.parse(routes_path)
    root_el = tree.getroot()
    timeline: list[tuple[float, str, str]] = []
    for child in root_el:
        if child.tag in {"vehicle", "person"}:
            time_attr = child.attrib.get("depart")
        elif child.tag == "flow":
            time_attr = child.attrib.get("begin")
        else:
            continue
        if time_attr is None:
            continue
        try:
            timeline.append((float(time_attr), child.tag, child.attrib.get("id", "?")))
        except ValueError:
            # Valores não-numéricos (ex.: depart="triggered") são ignorados.
            continue
    for i in range(1, len(timeline)):
        if timeline[i][0] < timeline[i - 1][0]:
            prev_t, prev_tag, prev_id = timeline[i - 1]
            cur_t, cur_tag, cur_id = timeline[i]
            raise SystemExit(
                f"Route file {routes_path} not sorted by departure: "
                f"<{cur_tag} id='{cur_id}' time={cur_t}> appears after "
                f"<{prev_tag} id='{prev_id}' time={prev_t}>. "
                f"Fix with: python $SUMO_HOME/tools/route/sort_routes.py "
                f"{routes_path} -o {routes_path}"
            )
    print(f"OK sorted: {routes_path.name} ({len(timeline)} time-dependent elements)")


def _require_number(holder: Dict[str, Any], key: str, context: str) -> float:
    value = holder.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SystemExit(f"Config inválida ({context}): '{key}' em falta ou não numérico.")
    return float(value)


def validate_safety_configs(root: Path) -> None:
    """Verifica invariantes numéricas safety-critical de cits/tsp config.

    Estes ficheiros não tinham validação semântica: uma inversão como
    green_extension_min_s > green_extension_max_s, ou max_total_green_s <
    min_green_s, era aceite em silêncio e só se manifestava (na melhor das
    hipóteses) como comportamento estranho da Safety Layer em runtime.
    Fail-closed: a primeira violação aborta com SystemExit.
    """
    cits = json.loads((root / "configs/cits_config.json").read_text(encoding="utf-8"))
    tsp = json.loads((root / "configs/tsp_config.json").read_text(encoding="utf-8"))

    safety = cits.get("safety_constraints", {})
    min_green = _require_number(safety, "min_green_s", "cits.safety_constraints")
    max_extension = _require_number(safety, "max_green_extension_s", "cits.safety_constraints")
    max_total_green = _require_number(safety, "max_total_green_s", "cits.safety_constraints")
    yellow_s = _require_number(safety, "yellow_s", "cits.safety_constraints")
    all_red_s = _require_number(safety, "all_red_s", "cits.safety_constraints")
    max_consecutive = _require_number(
        safety, "max_consecutive_priority_interventions_per_tls", "cits.safety_constraints"
    )
    cooldown_s = _require_number(safety, "cooldown_after_priority_s", "cits.safety_constraints")

    if min_green <= 0:
        raise SystemExit(f"Config inválida: min_green_s deve ser > 0 (é {min_green}).")
    if max_extension <= 0:
        raise SystemExit(f"Config inválida: max_green_extension_s deve ser > 0 (é {max_extension}).")
    if max_total_green < min_green:
        raise SystemExit(
            f"Config inválida: max_total_green_s ({max_total_green}) < min_green_s ({min_green})."
        )
    if yellow_s <= 0:
        raise SystemExit(f"Config inválida: yellow_s deve ser > 0 (é {yellow_s}).")
    if all_red_s < 0:
        raise SystemExit(f"Config inválida: all_red_s deve ser >= 0 (é {all_red_s}).")
    if max_consecutive < 1:
        raise SystemExit(
            f"Config inválida: max_consecutive_priority_interventions_per_tls deve ser >= 1 (é {max_consecutive})."
        )
    if cooldown_s < 0:
        raise SystemExit(f"Config inválida: cooldown_after_priority_s deve ser >= 0 (é {cooldown_s}).")

    policy = tsp.get("decision_policy", {})
    ge_min = _require_number(policy, "green_extension_min_s", "tsp.decision_policy")
    ge_default = _require_number(policy, "green_extension_default_s", "tsp.decision_policy")
    ge_max = _require_number(policy, "green_extension_max_s", "tsp.decision_policy")
    min_score = _require_number(policy, "min_priority_score", "tsp.decision_policy")
    early_green_min_eta = _require_number(policy, "early_green_min_eta_s", "tsp.decision_policy")
    red_truncation = _require_number(policy, "red_truncation_to_s", "tsp.decision_policy")

    if not (0.0 < ge_min <= ge_default <= ge_max):
        raise SystemExit(
            "Config inválida: green_extension deve respeitar "
            f"0 < min ({ge_min}) <= default ({ge_default}) <= max ({ge_max})."
        )
    if not 0.0 <= min_score <= 1.0:
        raise SystemExit(f"Config inválida: min_priority_score deve estar em [0,1] (é {min_score}).")
    if early_green_min_eta <= 0:
        raise SystemExit(f"Config inválida: early_green_min_eta_s deve ser > 0 (é {early_green_min_eta}).")
    if red_truncation <= 0:
        # Uma fase truncada para 0s (ou menos) é estruturalmente insegura.
        raise SystemExit(f"Config inválida: red_truncation_to_s deve ser > 0 (é {red_truncation}).")

    weights = policy.get("weights", {})
    if not isinstance(weights, dict) or not weights:
        raise SystemExit("Config inválida: tsp.decision_policy.weights em falta.")
    weight_sum = sum(float(v) for v in weights.values())
    if abs(weight_sum - 1.0) > 1e-6:
        raise SystemExit(
            f"Config inválida: a soma de decision_policy.weights deve ser 1.0 (é {weight_sum:.6f})."
        )

    for tls_id, mapping in tsp.get("phase_mapping", {}).items():
        if not isinstance(mapping, dict):
            continue
        corridor = mapping.get("corridor_green_phase_index")
        minor = mapping.get("minor_green_phase_index")
        if isinstance(corridor, int) and corridor < 0:
            raise SystemExit(f"Config inválida: phase_mapping[{tls_id}].corridor_green_phase_index < 0.")
        if isinstance(minor, int) and minor < 0:
            raise SystemExit(f"Config inválida: phase_mapping[{tls_id}].minor_green_phase_index < 0.")
        if (
            isinstance(corridor, int)
            and isinstance(minor, int)
            and corridor == minor
        ):
            raise SystemExit(
                f"Config inválida: phase_mapping[{tls_id}] tem corridor e minor no mesmo índice ({corridor})."
            )

    print("OK config: invariantes safety-critical de cits/tsp config verificadas.")


def validate(root: Path) -> None:
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing:
        raise SystemExit("Missing required files:\n" + "\n".join(missing))
    for rel in XML_FILES:
        ET.parse(root / rel)
        print(f"OK XML: {rel}")
    validate_routes_sorted(root / "sumo/routes/routes.rou.xml")
    validate_safety_configs(root)
    print("Static validation completed. Runtime validation with netconvert/sumo is still required.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    validate(args.root)


if __name__ == "__main__":
    main()
