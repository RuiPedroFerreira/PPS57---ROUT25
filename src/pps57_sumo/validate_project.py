#!/usr/bin/env python3
"""Static validation for the PPS57 SUMO base scenario.

This does not replace validation with SUMO. It checks the file structure, XML
well-formedness, and the safety-critical numeric invariants of the TSP/C-ITS
configuration files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.network_profile import load_network_profile  # noqa: E402
from pps57_sumo.scenarios import load_catalog, validate_scenario_catalog  # noqa: E402

REQUIRED_FILES = [
    "configs/sumo_scenario_base.json",
    "configs/scenario_catalog.yaml",
    "configs/cits_v2x_config.json",
    "configs/tsp_safety_config.json",
    "configs/policy_training_config.json",
    "sumo/plain/corredor.nod.xml",
    "sumo/plain/corredor.edg.xml",
    "sumo/routes/routes.rou.xml",
    "sumo/additional/bus_stops.add.xml",
    "sumo/additional/detectors.add.xml",
    "sumo/corredor.sumocfg",
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
        if child.tag in {"vehicle", "person", "trip"}:
            time_attr = child.attrib.get("depart")
        elif child.tag in {"flow", "personFlow"}:
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
    cits = json.loads((root / "configs/cits_v2x_config.json").read_text(encoding="utf-8"))
    tsp = json.loads((root / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
    sumo_base = json.loads((root / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))

    protocol_profile = cits.get("protocol_profile", {})
    if protocol_profile.get("version") != "0.4.0":
        raise SystemExit("Config inválida: protocol_profile.version deve ser 0.4.0.")
    expected_messages = {"MAPEM", "SPATEM", "SREM", "SSEM"}
    declared_messages = set(protocol_profile.get("messages", []))
    if declared_messages != expected_messages:
        raise SystemExit(
            "Config inválida: protocol_profile.messages deve conter exatamente MAPEM, SPATEM, SREM e SSEM."
        )
    scenario_id = str(cits.get("scenario_id", ""))
    if "v03" in scenario_id or "v0.3" in scenario_id:
        raise SystemExit("Config inválida: scenario_id ainda referencia v03/v0.3.")
    tsp_scenario_id = str(tsp.get("scenario_id", ""))
    if "v03" in tsp_scenario_id or "v0.3" in tsp_scenario_id:
        raise SystemExit("Config inválida: tsp.scenario_id ainda referencia v03/v0.3.")

    geometry = cits.get("synthetic_geometry", {})
    if not isinstance(geometry, dict):
        raise SystemExit("Config inválida: synthetic_geometry deve ser objeto.")
    if bool(geometry.get("enabled", False)):
        lat = _require_number(geometry, "origin_latitude_e7", "cits.synthetic_geometry")
        lon = _require_number(geometry, "origin_longitude_e7", "cits.synthetic_geometry")
        spacing = _require_number(geometry, "intersection_spacing_e7", "cits.synthetic_geometry")
        lateral = _require_number(geometry, "lateral_offset_e7", "cits.synthetic_geometry")
        _require_number(geometry, "elevation_dm", "cits.synthetic_geometry")
        if not -900000000 <= lat <= 900000000:
            raise SystemExit("Config inválida: synthetic_geometry.origin_latitude_e7 fora de range CDD.")
        if not -1800000000 <= lon <= 1800000000:
            raise SystemExit("Config inválida: synthetic_geometry.origin_longitude_e7 fora de range CDD.")
        if spacing < 0 or lateral < 0:
            raise SystemExit("Config inválida: synthetic_geometry spacing/lateral devem ser >= 0.")

    transport = cits.get("message_transport", {})
    if not isinstance(transport, dict):
        raise SystemExit("Config inválida: message_transport deve ser objeto.")
    for key in ("enabled", "encode_payloads"):
        value = transport.get(key, False)
        if not isinstance(value, bool):
            raise SystemExit(f"Config inválida: message_transport.{key} deve ser booleano.")
    for key in ("latency_steps", "jitter_steps", "reorder_window_steps", "random_seed"):
        value = transport.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SystemExit(f"Config inválida: message_transport.{key} deve ser inteiro >= 0.")
    for key in ("drop_rate", "duplicate_rate"):
        value = transport.get(key, 0.0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= float(value) <= 1.0:
            raise SystemExit(f"Config inválida: message_transport.{key} deve estar em [0,1].")

    trust_store = cits.get("trust_store", {})
    if not isinstance(trust_store, dict):
        raise SystemExit("Config inválida: trust_store deve ser objeto.")
    if trust_store.get("mode", "tofu") not in {"tofu", "prefix_allowlist"}:
        raise SystemExit("Config inválida: trust_store.mode deve ser tofu ou prefix_allowlist.")
    if trust_store.get("mode") == "prefix_allowlist" and not trust_store.get("allowed_signer_prefixes"):
        raise SystemExit("Config inválida: prefix_allowlist requer allowed_signer_prefixes.")

    sumo_intersection_types = {
        str(item.get("id")): str(item.get("type", "traffic_light"))
        for item in sumo_base.get("network", {}).get("intersections", [])
        if isinstance(item, dict) and item.get("id")
    }
    for index, intersection in enumerate(cits.get("intersections", [])):
        intersection_id = str(intersection.get("intersection_id", ""))
        signal_controlled = intersection.get("signal_controlled", True)
        if not isinstance(signal_controlled, bool):
            raise SystemExit(
                f"Config inválida: cits.intersections[{index}].signal_controlled deve ser boolean."
            )
        sumo_type = sumo_intersection_types.get(intersection_id)
        if sumo_type == "traffic_light" and not signal_controlled:
            raise SystemExit(
                f"Config inválida: {intersection_id} é traffic_light no SUMO mas signal_controlled=false no C-ITS."
            )
        if sumo_type and sumo_type != "traffic_light" and signal_controlled:
            raise SystemExit(
                f"Config inválida: {intersection_id} é '{sumo_type}' no SUMO mas signal_controlled=true no C-ITS."
            )
        if not signal_controlled and intersection.get("priority_movements"):
            raise SystemExit(
                f"Config inválida: {intersection_id} não é semaforizada mas declara priority_movements C-ITS."
            )

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

    # priority_level_weights e actuating_actions: chaves extraídas para config
    # em P0. São opcionais (código recai em defaults), mas quando presentes têm
    # de ser coerentes com os enums — um typo numa classe cairia silenciosamente
    # no peso 0.0, e uma ação inválida nunca atuaria.
    from pps57_cits.messages import OperatorPriorityClass
    from pps57_tsp.models import TSPAction

    priority_level_weights = policy.get("priority_level_weights")
    if priority_level_weights is not None:
        expected_classes = {member.value for member in OperatorPriorityClass}
        if not isinstance(priority_level_weights, dict) or not priority_level_weights:
            raise SystemExit(
                "Config inválida: decision_policy.priority_level_weights deve ser objeto não vazio."
            )
        actual_classes = set(priority_level_weights.keys())
        if actual_classes != expected_classes:
            raise SystemExit(
                "Config inválida: decision_policy.priority_level_weights deve ter exatamente as classes "
                f"{sorted(expected_classes)} (tem {sorted(actual_classes)})."
            )
        for class_name, weight in priority_level_weights.items():
            if (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not 0.0 <= float(weight) <= 1.0
            ):
                raise SystemExit(
                    f"Config inválida: decision_policy.priority_level_weights['{class_name}'] deve estar em [0,1]."
                )

    actuating_actions = policy.get("actuating_actions")
    if actuating_actions is not None:
        valid_actions = {member.value for member in TSPAction}
        if not isinstance(actuating_actions, list) or not actuating_actions:
            raise SystemExit(
                "Config inválida: decision_policy.actuating_actions deve ser lista não vazia."
            )
        unknown_actions = [action for action in actuating_actions if action not in valid_actions]
        if unknown_actions:
            raise SystemExit(
                f"Config inválida: decision_policy.actuating_actions contém ações inválidas: {unknown_actions}."
            )

    # Reward keys (policy_training_config.json) são tuning, não safety-critical:
    # validar só que são numéricas. Guardado por existência do ficheiro porque os
    # testes do validador constroem roots temporários só com os 3 configs base.
    policy_training_path = root / "configs/policy_training_config.json"
    if policy_training_path.exists():
        policy_training = json.loads(policy_training_path.read_text(encoding="utf-8"))
        reward_cfg = policy_training.get("reward", {})
        if not isinstance(reward_cfg, dict):
            raise SystemExit("Config inválida: policy_training_config.reward deve ser objeto.")
        for key, value in reward_cfg.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SystemExit(
                    f"Config inválida: policy_training_config.reward['{key}'] deve ser numérico (é {value!r})."
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
        if not intersection.get("signal_controlled", True):
            continue
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

    validate_network_profile_config(root, cits, tsp)
    print("OK config: invariantes safety-critical de cits/tsp config verificadas.")


def validate_network_profile_config(root: Path, cits: Dict[str, Any], tsp: Dict[str, Any]) -> None:
    cits_discovery = cits.get("network_discovery", {})
    tsp_profile = tsp.get("network_profile", {})
    enabled = (
        isinstance(cits_discovery, dict)
        and bool(cits_discovery.get("enabled", False))
    ) or (
        isinstance(tsp_profile, dict)
        and bool(tsp_profile.get("enabled", False))
    )
    if not enabled:
        return
    sumo_cfg = cits.get("sumo", {})
    if not isinstance(sumo_cfg, dict) or not sumo_cfg.get("network"):
        raise SystemExit("Config invalida: network_discovery/network_profile requer cits.sumo.network.")
    network_path = Path(str(sumo_cfg["network"]))
    if not network_path.is_absolute():
        network_path = root / network_path
    if not network_path.exists() and network_path.parent == root / "sumo" / "network":
        print(f"SKIP network profile: generated net.xml not found at {network_path}; run make build to generate it.")
        return
    if not network_path.exists():
        raise SystemExit(f"Config invalida: network profile net.xml nao existe: {network_path}")
    try:
        profile = load_network_profile(network_path)
    except Exception as exc:
        raise SystemExit(f"Config invalida: nao foi possivel ler network profile de {network_path}: {exc}") from exc
    if not profile.tls_profiles:
        raise SystemExit(f"Config invalida: network profile sem tlLogic/controladores em {network_path}.")

    profile_tls = set(profile.tls_profiles)
    for index, intersection in enumerate(cits.get("intersections", [])):
        if not isinstance(intersection, dict) or not bool(intersection.get("signal_controlled", True)):
            continue
        tls_id = str(intersection.get("tls_id", ""))
        if tls_id and tls_id not in profile_tls:
            raise SystemExit(
                f"Config invalida: cits.intersections[{index}].tls_id={tls_id!r} "
                f"nao existe no net.xml perfilado."
            )


def validate_scenario_profiles(root: Path) -> None:
    config = json.loads((root / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
    catalog = load_catalog(root / "configs/scenario_catalog.yaml")
    summaries = validate_scenario_catalog(config, catalog)
    if not summaries:
        raise SystemExit("Config inválida: nenhum cenário SUMO validado.")
    weak = [
        summary["scenario_id"]
        for summary in summaries
        if summary["estimated_car_departures"] <= 0 or summary["estimated_bus_departures"] <= 0
    ]
    if weak:
        raise SystemExit("Config inválida: cenários sem procura operacional: " + ", ".join(weak))
    print(f"OK scenarios: {len(summaries)} scenario profiles validated.")


def validate(root: Path) -> None:
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing:
        raise SystemExit("Missing required files:\n" + "\n".join(missing))
    for rel in XML_FILES:
        ET.parse(root / rel)
        print(f"OK XML: {rel}")
    validate_routes_sorted(root / "sumo/routes/routes.rou.xml")
    validate_safety_configs(root)
    validate_scenario_profiles(root)
    print("Static validation completed. Runtime validation with netconvert/sumo is still required.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    validate(args.root)


if __name__ == "__main__":
    main()
