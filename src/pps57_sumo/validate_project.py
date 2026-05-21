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
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

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

    phase_mapping = tsp.get("phase_mapping", {})
    for tls_id, mapping in phase_mapping.items():
        if tls_id == "priority_movements":
            continue
        if not isinstance(mapping, dict):
            continue
        target = mapping.get("target_phase_index")
        if isinstance(target, int) and target < 0:
            raise SystemExit(f"Config inválida: phase_mapping[{tls_id}].target_phase_index < 0.")
        for idx in mapping.get("service_green_phase_indices", []):
            if not isinstance(idx, int) or idx < 0:
                raise SystemExit(f"Config inválida: phase_mapping[{tls_id}].service_green_phase_indices inválido.")

    declared_movements = {}
    for index, intersection in enumerate(cits.get("intersections", [])):
        controlled_edges = set(intersection.get("controlled_approach_edges", []))
        for movement in intersection.get("priority_movements", []):
            movement_id = movement.get("movement_id")
            if not movement_id:
                raise SystemExit(f"Config inválida: intersections[{index}].priority_movements sem movement_id.")
            if movement_id in declared_movements:
                raise SystemExit(f"Config inválida: priority movement duplicado: {movement_id}.")
            declared_movements[movement_id] = movement
            approach_edges = movement.get("approach_edges", [])
            if not approach_edges:
                raise SystemExit(f"Config inválida: priority movement {movement_id} sem approach_edges.")
            unknown_edges = [edge for edge in approach_edges if edge not in controlled_edges]
            if unknown_edges:
                raise SystemExit(
                    f"Config inválida: priority movement {movement_id} referencia edges fora da interseção: "
                    + ", ".join(unknown_edges)
                )
            if not movement.get("target_signal_group_id"):
                raise SystemExit(f"Config inválida: priority movement {movement_id} sem target_signal_group_id.")

    movement_phase_mapping = phase_mapping.get("priority_movements", {})
    missing_mappings = [movement_id for movement_id in declared_movements if movement_id not in movement_phase_mapping]
    if missing_mappings:
        raise SystemExit(
            "Config inválida: faltam mappings tsp.phase_mapping.priority_movements para: "
            + ", ".join(missing_mappings)
        )
    for movement_id, mapping in movement_phase_mapping.items():
        if movement_id not in declared_movements:
            raise SystemExit(f"Config inválida: mapping TSP para movement inexistente: {movement_id}.")
        if not isinstance(mapping, dict) or not isinstance(mapping.get("target_phase_index"), int):
            raise SystemExit(f"Config inválida: priority movement {movement_id} sem target_phase_index inteiro.")
        if mapping["target_phase_index"] < 0:
            raise SystemExit(f"Config inválida: priority movement {movement_id}.target_phase_index < 0.")

    controller_default = tsp.get("controller_contracts", {}).get("default", {})
    if not isinstance(controller_default, dict):
        raise SystemExit("Config inválida: tsp.controller_contracts.default em falta.")
    for key in ("adapter_type", "allowed_actions", "phase_sequence", "intergreen_phase_indices"):
        if key not in controller_default:
            raise SystemExit(f"Config inválida: controller_contracts.default.{key} em falta.")
    if not controller_default.get("allowed_actions"):
        raise SystemExit("Config inválida: controller_contracts.default.allowed_actions vazio.")
    for idx in controller_default.get("phase_sequence", []) + controller_default.get("intergreen_phase_indices", []):
        if not isinstance(idx, int) or idx < 0:
            raise SystemExit("Config inválida: controller_contracts.default contém índice de fase inválido.")

    priority_group_defaults = controller_default.get("priority_signal_group_defaults", {})
    if not isinstance(priority_group_defaults, dict):
        raise SystemExit("Config inválida: priority_signal_group_defaults em falta.")
    if not priority_group_defaults.get("conflicts_with"):
        raise SystemExit("Config inválida: priority_signal_group_defaults sem matriz de conflitos.")
    for key in ("min_green_s", "max_green_s", "max_extension_s"):
        value = priority_group_defaults.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            raise SystemExit(f"Config inválida: priority_signal_group_defaults.{key} inválido.")
    if priority_group_defaults["max_green_s"] < priority_group_defaults["min_green_s"]:
        raise SystemExit("Config inválida: priority_signal_group_defaults max_green_s < min_green_s.")
    if not controller_default.get("additional_signal_groups"):
        raise SystemExit("Config inválida: controller_contracts.default.additional_signal_groups vazio.")
    controllers = tsp.get("controller_contracts", {}).get("controllers", {})
    if not isinstance(controllers, dict) or not controllers:
        raise SystemExit("Config inválida: controller_contracts.controllers em falta.")
    for intersection in cits.get("intersections", []):
        tls_id = intersection.get("tls_id")
        controller = controllers.get(tls_id)
        if not isinstance(controller, dict):
            raise SystemExit(f"Config inválida: controller_contracts.controllers[{tls_id}] em falta.")
        group_ids = {movement["target_signal_group_id"] for movement in intersection.get("priority_movements", [])}
        group_ids.update(
            str(item.get("signal_group_id"))
            for item in controller.get("additional_signal_groups", [])
            if item.get("signal_group_id")
        )
        explicit_groups = controller.get("signal_groups", {})
        if not isinstance(explicit_groups, dict):
            raise SystemExit(f"Config inválida: controller {tls_id} signal_groups inválido.")
        for movement in intersection.get("priority_movements", []):
            group_id = movement["target_signal_group_id"]
            if group_id not in explicit_groups:
                raise SystemExit(f"Config inválida: controller {tls_id} sem signal_group específico {group_id}.")
        for group_id, group in explicit_groups.items():
            conflicts = group.get("conflicts_with", [])
            if not conflicts:
                raise SystemExit(f"Config inválida: controller {tls_id} signal_group {group_id} sem conflitos.")
            unknown = [item for item in conflicts if item not in group_ids]
            if unknown:
                raise SystemExit(
                    f"Config inválida: controller {tls_id} signal_group {group_id} tem conflitos inexistentes: "
                    + ", ".join(unknown)
                )
        for group in controller.get("additional_signal_groups", []):
            group_id = group.get("signal_group_id")
            conflicts = group.get("conflicts_with", [])
            if group_id and not conflicts:
                raise SystemExit(f"Config inválida: controller {tls_id} signal_group {group_id} sem conflitos.")
            unknown = [item for item in conflicts if item not in group_ids]
            if unknown:
                raise SystemExit(
                    f"Config inválida: controller {tls_id} signal_group {group_id} tem conflitos inexistentes: "
                    + ", ".join(unknown)
                )
    simulation_cfg = tsp.get("controller_simulation", {})
    if not isinstance(simulation_cfg, dict):
        raise SystemExit("Config inválida: controller_simulation deve ser objeto.")
    for key in ("command_latency_s", "pending_lock_s", "min_command_interval_s"):
        value = simulation_cfg.get(key, 0)
        if not isinstance(value, (int, float)) or value < 0:
            raise SystemExit(f"Config inválida: controller_simulation.{key} deve ser >= 0.")

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
