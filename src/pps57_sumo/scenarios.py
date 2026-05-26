#!/usr/bin/env python3
"""Scenario profile loading and validation for PPS57 SUMO runs."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import random
from pathlib import Path
from typing import Any

import yaml


class ScenarioConfigError(ValueError):
    """Raised when a scenario profile cannot be applied safely."""


def load_catalog(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ScenarioConfigError(f"Scenario catalog must be a mapping: {path}")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise ScenarioConfigError("Scenario catalog must define a non-empty 'scenarios' mapping.")
    return data


def apply_scenario_profile(base_config: dict[str, Any], scenario_id: str | None) -> dict[str, Any]:
    """Return a scenario-specific config without mutating the base config."""
    config = deepcopy(base_config)
    if not scenario_id:
        return config

    profiles = config.get("scenario_profiles", {})
    if scenario_id not in profiles:
        raise ScenarioConfigError(f"Unknown scenario profile: {scenario_id}")
    profile = profiles[scenario_id]
    if not isinstance(profile, dict):
        raise ScenarioConfigError(f"Scenario profile must be a mapping: {scenario_id}")

    config["scenario_id"] = scenario_id
    config["scenario_profile"] = {
        key: deepcopy(value)
        for key, value in profile.items()
        if key in {"description", "justification", "kpi_focus", "expected_behaviour", "tags", "random_seeds"}
    }
    if "simulation_end_s" in profile:
        config["simulation_end_s"] = profile["simulation_end_s"]
    if "random_seed" in profile:
        config["random_seed"] = profile["random_seed"]

    demand_profile = str(profile.get("demand_profile", config.get("active_demand_profile", "am_peak")))
    config["active_demand_profile"] = demand_profile
    config["demand_profiles"] = resolve_demand_profiles(config.get("demand_profiles", {}))

    _apply_public_transport_overrides(config, profile)
    _apply_vehicle_overrides(config, profile)
    _apply_vehicle_distribution_overrides(config, profile)
    if "events" in profile:
        config["events"] = deepcopy(profile["events"])
    _materialize_stochastic_incidents(config, profile, scenario_id=scenario_id)

    validate_scenario_config(config, scenario_id=scenario_id)
    return config


def resolve_demand_profiles(profiles: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    resolving: set[str] = set()

    def resolve_one(name: str) -> dict[str, Any]:
        if name in resolved:
            return deepcopy(resolved[name])
        if name in resolving:
            raise ScenarioConfigError(f"Cyclic demand profile derivation at {name}")
        if name not in profiles:
            raise ScenarioConfigError(f"Unknown demand profile: {name}")
        profile = profiles[name]
        if not isinstance(profile, dict):
            raise ScenarioConfigError(f"Demand profile must be a mapping: {name}")
        resolving.add(name)
        if "derived_from" in profile:
            parent = resolve_one(str(profile["derived_from"]))
            flows = _derive_flows(parent.get("flows", []), profile, profile_name=name)
            derived = deepcopy(parent)
            derived.update({key: deepcopy(value) for key, value in profile.items() if key not in {"derived_from", "flows"}})
            derived["flows"] = flows
            resolved[name] = derived
        else:
            resolved[name] = deepcopy(profile)
        resolving.remove(name)
        return deepcopy(resolved[name])

    for profile_name in profiles:
        resolve_one(profile_name)
    return resolved


def validate_scenario_catalog(base_config: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate all catalog scenarios against the generator config.

    Returns lightweight summaries used by tests and static validation.
    """
    summaries: list[dict[str, Any]] = []
    profile_ids = set(base_config.get("scenario_profiles", {}))
    for scenario_id, entry in catalog.get("scenarios", {}).items():
        if scenario_id not in profile_ids:
            raise ScenarioConfigError(f"Catalog scenario '{scenario_id}' has no matching scenario_profiles entry.")
        if not isinstance(entry, dict):
            raise ScenarioConfigError(f"Catalog scenario '{scenario_id}' must be a mapping.")
        for key in ("description", "realism_basis", "kpi_focus"):
            if not entry.get(key):
                raise ScenarioConfigError(f"Catalog scenario '{scenario_id}' missing required field '{key}'.")
        config = apply_scenario_profile(base_config, scenario_id)
        summaries.append(scenario_summary(config))
    return summaries


def scenario_summary(config: dict[str, Any]) -> dict[str, Any]:
    demand = config.get("demand_profiles", {}).get(config.get("active_demand_profile", ""), {})
    flows = demand.get("flows", [])
    services = config.get("public_transport", {}).get("services", [])
    events = config.get("events", [])
    return {
        "scenario_id": config.get("scenario_id"),
        "active_demand_profile": config.get("active_demand_profile"),
        "flow_count": sum(_expanded_flow_count(flow) for flow in flows),
        "estimated_car_departures": sum(_estimated_flow_departures(flow) for flow in flows),
        "estimated_bus_departures": sum(_estimated_service_departures(service, config) for service in services),
        "event_count": len(events),
        "kpi_focus": config.get("scenario_profile", {}).get("kpi_focus", []),
    }


def validate_scenario_config(config: dict[str, Any], *, scenario_id: str | None = None) -> None:
    context = scenario_id or str(config.get("scenario_id", "<base>"))
    profiles = resolve_demand_profiles(config.get("demand_profiles", {}))
    demand_profile = str(config.get("active_demand_profile", ""))
    if demand_profile not in profiles:
        raise ScenarioConfigError(f"{context}: active demand profile '{demand_profile}' is not defined.")

    route_ids = _known_route_ids(config)
    flows = profiles[demand_profile].get("flows", [])
    if not flows:
        raise ScenarioConfigError(f"{context}: demand profile '{demand_profile}' has no flows.")
    for flow in flows:
        _validate_time_window(flow, context=f"{context}: flow {flow.get('id')}")
        period = float(flow.get("period", 0))
        if period <= 0:
            raise ScenarioConfigError(f"{context}: flow {flow.get('id')} must have period > 0.")
        if flow.get("route") not in route_ids:
            raise ScenarioConfigError(f"{context}: flow {flow.get('id')} references unknown route {flow.get('route')}.")
        for index, entry in enumerate(flow.get("time_profile", []) or []):
            entry_context = f"{context}: flow {flow.get('id')} time_profile[{index}]"
            _validate_time_window(entry, context=entry_context)
            if "period" in entry and float(entry["period"]) <= 0:
                raise ScenarioConfigError(f"{entry_context}: period must be > 0.")
            if "scale" in entry and float(entry["scale"]) <= 0:
                raise ScenarioConfigError(f"{entry_context}: scale must be > 0.")

    stop_ids = {str(stop["id"]) for stop in config.get("public_transport", {}).get("stops", [])}
    for stop in config.get("public_transport", {}).get("stops", []):
        spec = stop.get("dwell_s", 20)
        if isinstance(spec, dict):
            mean = float(spec.get("mean", 0))
            std = float(spec.get("std", 0))
            lo = float(spec.get("min", 0))
            hi = float(spec.get("max", mean + 3 * std if std else mean))
            if mean <= 0:
                raise ScenarioConfigError(f"{context}: stop {stop.get('id')} dwell_s.mean must be > 0.")
            if std < 0:
                raise ScenarioConfigError(f"{context}: stop {stop.get('id')} dwell_s.std must be >= 0.")
            if lo > hi:
                raise ScenarioConfigError(f"{context}: stop {stop.get('id')} dwell_s.min must be <= dwell_s.max.")
        elif float(spec) <= 0:
            raise ScenarioConfigError(f"{context}: stop {stop.get('id')} dwell_s must be > 0.")

    for service in config.get("public_transport", {}).get("services", []):
        _validate_time_window(
            {"begin": service.get("begin_s", config.get("simulation_begin_s", 0)), "end": service.get("end_s", config.get("simulation_end_s", 7200))},
            context=f"{context}: service {service.get('line_id')} {service.get('direction')}",
        )
        if float(service.get("headway_s", 0)) <= 0:
            raise ScenarioConfigError(f"{context}: service {service.get('line_id')} {service.get('direction')} must have headway_s > 0.")
        if service.get("route") not in route_ids:
            raise ScenarioConfigError(f"{context}: service {service.get('line_id')} references unknown route {service.get('route')}.")
        unknown_stops = [stop_id for stop_id in service.get("stops", []) if str(stop_id) not in stop_ids]
        if unknown_stops:
            raise ScenarioConfigError(f"{context}: service {service.get('line_id')} references unknown stops: {unknown_stops}")
        if float(service.get("terminus_jitter_s", 0)) < 0:
            raise ScenarioConfigError(f"{context}: service {service.get('line_id')} {service.get('direction')} terminus_jitter_s must be >= 0.")
        for index, entry in enumerate(service.get("headway_schedule", []) or []):
            entry_context = f"{context}: service {service.get('line_id')} {service.get('direction')} headway_schedule[{index}]"
            entry_window = {"begin": entry.get("begin_s", 0), "end": entry.get("end_s", 0)}
            _validate_time_window(entry_window, context=entry_context)
            if float(entry.get("headway_s", 0)) <= 0:
                raise ScenarioConfigError(f"{entry_context}: headway_s must be > 0.")

    for event in config.get("events", []):
        if event.get("route") not in route_ids:
            raise ScenarioConfigError(f"{context}: event {event.get('id')} references unknown route {event.get('route')}.")
        depart = float(event.get("depart", 0))
        begin = float(config.get("simulation_begin_s", 0))
        end = float(config.get("simulation_end_s", 7200))
        if not begin <= depart <= end:
            raise ScenarioConfigError(f"{context}: event {event.get('id')} departs outside simulation window.")


def _derive_flows(parent_flows: list[dict[str, Any]], profile: dict[str, Any], *, profile_name: str) -> list[dict[str, Any]]:
    period_scale = float(profile.get("period_scale", 1.0))
    if period_scale <= 0:
        raise ScenarioConfigError(f"Demand profile {profile_name}: period_scale must be > 0.")
    flow_period_scale = {str(key): float(value) for key, value in profile.get("flow_period_scale", {}).items()}
    route_period_scale = {str(key): float(value) for key, value in profile.get("route_period_scale", {}).items()}
    flow_period_override = {str(key): float(value) for key, value in profile.get("flow_period_override", {}).items()}
    flow_updates = {str(item["id"]): item for item in profile.get("flow_updates", [])}
    remove_flows = {str(item) for item in profile.get("remove_flows", [])}
    id_suffix = str(profile.get("id_suffix", profile_name))
    flows: list[dict[str, Any]] = []

    for base_flow in parent_flows:
        flow_id = str(base_flow["id"])
        if flow_id in remove_flows:
            continue
        flow = deepcopy(base_flow)
        scale = period_scale
        scale *= route_period_scale.get(str(flow.get("route")), 1.0)
        scale *= flow_period_scale.get(flow_id, 1.0)
        if scale <= 0:
            raise ScenarioConfigError(f"Demand profile {profile_name}: flow scale for {flow_id} must be > 0.")
        flow["id"] = f"{flow_id}_{id_suffix}"
        flow["period"] = round(float(flow["period"]) * scale, 3)
        if "time_profile" in flow and isinstance(flow["time_profile"], list):
            scaled_profile = []
            for entry in flow["time_profile"]:
                entry_copy = deepcopy(entry)
                if "period" in entry_copy:
                    entry_copy["period"] = round(float(entry_copy["period"]) * scale, 3)
                scaled_profile.append(entry_copy)
            flow["time_profile"] = scaled_profile
        if "begin" in profile:
            flow["begin"] = profile["begin"]
        if "end" in profile:
            flow["end"] = profile["end"]
        if flow_id in flow_period_override:
            flow["period"] = flow_period_override[flow_id]
            flow.pop("time_profile", None)
        if flow_id in flow_updates:
            for key, value in flow_updates[flow_id].items():
                if key != "id":
                    flow[key] = deepcopy(value)
        flows.append(flow)

    for flow in profile.get("additional_flows", []):
        flows.append(deepcopy(flow))
    return flows


def _apply_vehicle_overrides(config: dict[str, Any], profile: dict[str, Any]) -> None:
    """Apply scenario-level overrides to vehicle types (e.g., weather effects).

    Override block schema::

        vehicle_overrides:
          all:
            tau_delta: 0.2                # additive seconds
            speed_factor_multiplier: 0.88 # scales the mean of normc(...)
            decel_multiplier: 0.85
            accel_multiplier: 0.95
            min_gap_multiplier: 1.15
          by_class:                        # overrides per vClass (e.g., motorcycle)
            motorcycle: {speed_factor_multiplier: 0.80}
          by_id:                           # overrides per vType id (e.g., car_aggressive)
            car_aggressive: {tau_delta: 0.3}
    """
    overrides = profile.get("vehicle_overrides")
    if not isinstance(overrides, dict) or not overrides:
        return
    all_override = overrides.get("all", {}) if isinstance(overrides.get("all"), dict) else {}
    by_class = overrides.get("by_class", {}) if isinstance(overrides.get("by_class"), dict) else {}
    by_id = overrides.get("by_id", {}) if isinstance(overrides.get("by_id"), dict) else {}
    vehicle_types = config.get("vehicle_types", [])
    if not isinstance(vehicle_types, list):
        return
    for vtype in vehicle_types:
        if not isinstance(vtype, dict):
            continue
        merged: dict[str, Any] = {}
        merged.update(all_override)
        merged.update(by_class.get(str(vtype.get("vClass", "")), {}) or {})
        merged.update(by_id.get(str(vtype.get("id", "")), {}) or {})
        if not merged:
            continue
        if "tau_delta" in merged:
            vtype["tau"] = round(float(vtype.get("tau", 1.0)) + float(merged["tau_delta"]), 3)
        if "accel_multiplier" in merged and "accel" in vtype:
            vtype["accel"] = round(float(vtype["accel"]) * float(merged["accel_multiplier"]), 3)
        if "decel_multiplier" in merged and "decel" in vtype:
            vtype["decel"] = round(float(vtype["decel"]) * float(merged["decel_multiplier"]), 3)
            if "emergencyDecel" in vtype:
                vtype["emergencyDecel"] = round(float(vtype["emergencyDecel"]) * float(merged["decel_multiplier"]), 3)
        if "min_gap_multiplier" in merged and "minGap" in vtype:
            vtype["minGap"] = round(float(vtype["minGap"]) * float(merged["min_gap_multiplier"]), 3)
        if "max_speed_multiplier" in merged and "maxSpeed" in vtype:
            vtype["maxSpeed"] = round(float(vtype["maxSpeed"]) * float(merged["max_speed_multiplier"]), 3)
        if "speed_factor_multiplier" in merged:
            vtype["speedFactor"] = _scale_speed_factor(vtype.get("speedFactor"), float(merged["speed_factor_multiplier"]))


def _scale_speed_factor(spec: Any, multiplier: float) -> Any:
    """Scale the *mean* of a SUMO speedFactor expression by ``multiplier``.

    Supports two common forms:
      * a numeric literal (``"1.05"`` or ``1.05``)
      * ``"normc(mean,std,lo,hi)"`` — scales mean and clamps lo/hi proportionally.

    Returns the original spec unchanged if it is not parseable, so callers
    never silently corrupt an unrecognised expression.
    """
    if spec is None or multiplier == 1.0:
        return spec
    if isinstance(spec, (int, float)):
        return round(float(spec) * multiplier, 4)
    text = str(spec).strip()
    if text.startswith("normc(") and text.endswith(")"):
        body = text[len("normc("):-1]
        parts = [p.strip() for p in body.split(",")]
        if len(parts) == 4:
            try:
                mean, std, lo, hi = (float(p) for p in parts)
            except ValueError:
                return spec
            new_mean = mean * multiplier
            new_lo = lo * multiplier
            new_hi = hi * multiplier
            return f"normc({new_mean:.3f},{std:.3f},{new_lo:.3f},{new_hi:.3f})"
    try:
        return round(float(text) * multiplier, 4)
    except ValueError:
        return spec


def _apply_vehicle_distribution_overrides(config: dict[str, Any], profile: dict[str, Any]) -> None:
    """Replace component lists on named vTypeDistribution(s) at scenario time.

    Schema::

        vehicle_distribution_overrides:
          urban_mix:
            - {type: car, probability: 0.30}
            - {type: car_acc, probability: 0.15}
            ...

    Probabilities are not auto-normalised — caller is responsible. We validate
    that referenced vTypes exist in ``vehicle_types`` so a typo fails loud.
    """
    overrides = profile.get("vehicle_distribution_overrides")
    if not isinstance(overrides, dict) or not overrides:
        return
    distributions = config.get("vehicle_type_distributions", [])
    known_vtype_ids = {str(vt["id"]) for vt in config.get("vehicle_types", []) if isinstance(vt, dict) and "id" in vt}
    for dist in distributions:
        if not isinstance(dist, dict):
            continue
        dist_id = str(dist.get("id", ""))
        if dist_id not in overrides:
            continue
        new_components = overrides[dist_id]
        if not isinstance(new_components, list) or not new_components:
            raise ScenarioConfigError(
                f"vehicle_distribution_overrides[{dist_id}] must be a non-empty list of components."
            )
        for component in new_components:
            type_id = str(component.get("type", ""))
            if type_id not in known_vtype_ids:
                raise ScenarioConfigError(
                    f"vehicle_distribution_overrides[{dist_id}] references unknown vType '{type_id}'."
                )
        dist["components"] = deepcopy(new_components)


def _materialize_stochastic_incidents(
    config: dict[str, Any], profile: dict[str, Any], *, scenario_id: str | None
) -> None:
    """Turn ``stochastic_incidents`` templates into deterministic events using ``random_seed``.

    Each template describes a probability + sampling parameters. A per-scenario
    RNG seeded from the scenario_id and config random_seed decides whether the
    incident fires and at which edge/time/duration. Results are appended to
    ``config["events"]`` so downstream code treats them identically to manually
    authored events.
    """
    templates = profile.get("stochastic_incidents")
    if not isinstance(templates, list) or not templates:
        return
    base_seed = int(config.get("random_seed", 57))
    rng = random.Random(_stochastic_incident_seed(base_seed, scenario_id))
    events = list(config.get("events", []))
    for index, template in enumerate(templates):
        if not isinstance(template, dict):
            continue
        probability = float(template.get("probability", 1.0))
        if rng.random() > probability:
            continue
        edges = template.get("edge_candidates") or []
        routes = template.get("route_candidates") or []
        if not edges or not routes:
            continue
        edge_id = rng.choice(list(edges))
        route_id = rng.choice(list(routes))
        depart_window = template.get("depart_window_s", [0, 3600])
        if not isinstance(depart_window, list) or len(depart_window) != 2:
            depart_window = [0, 3600]
        depart_s = rng.uniform(float(depart_window[0]), float(depart_window[1]))
        duration_mean = float(template.get("duration_s_mean", 240.0))
        duration_std = float(template.get("duration_s_std", 0.0))
        if duration_std > 0:
            duration_s = max(30.0, rng.gauss(duration_mean, duration_std))
        else:
            duration_s = duration_mean
        event_id = f"{template.get('id_prefix', 'stoch_incident')}_{index}_{int(depart_s)}"
        events.append(
            {
                "id": event_id,
                "type": str(template.get("type", "stopped_vehicle")),
                "vehicle_type": str(template.get("vehicle_type", "lcv")),
                "route": route_id,
                "depart": round(depart_s, 1),
                "stop_edge": edge_id,
                "stop_duration_s": round(duration_s, 1),
                "stop_pos_m": float(template.get("stop_pos_m", 80.0)),
                "description": f"Stochastic incident materialised from template '{template.get('id_prefix', '')}'.",
            }
        )
    config["events"] = events


def _stochastic_incident_seed(base_seed: int, scenario_id: str | None) -> int:
    key = f"{base_seed}:{scenario_id or ''}:stochastic_incidents"
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _apply_public_transport_overrides(config: dict[str, Any], profile: dict[str, Any]) -> None:
    pt = config.get("public_transport", {})
    stop_overrides = {str(item["id"]): item for item in profile.get("stop_overrides", [])}
    for stop in pt.get("stops", []):
        override = stop_overrides.get(str(stop["id"]))
        if override:
            for key, value in override.items():
                if key != "id":
                    stop[key] = deepcopy(value)

    service_overrides = profile.get("service_overrides", [])
    for override in service_overrides:
        matched = False
        for service in pt.get("services", []):
            if _service_matches(service, override):
                for key, value in override.items():
                    if key not in {"line_id", "direction", "line_code"}:
                        service[key] = deepcopy(value)
                matched = True
        if not matched:
            raise ScenarioConfigError(f"Service override did not match any service: {override}")


def _service_matches(service: dict[str, Any], override: dict[str, Any]) -> bool:
    if "line_code" in override:
        return str(service.get("line_code")) == str(override["line_code"])
    return (
        str(service.get("line_id")) == str(override.get("line_id"))
        and str(service.get("direction")) == str(override.get("direction"))
    )


def _known_route_ids(config: dict[str, Any]) -> set[str]:
    intersections = config.get("network", {}).get("intersections", [])
    route_ids = {
        "route_boavista_east_to_west",
        "route_boavista_west_to_east",
        "route_emergency_west_to_east",
    }
    for inter in intersections:
        inter_id = inter["id"]
        route_ids.add(f"route_cross_NS_{inter_id}")
        route_ids.add(f"route_cross_SN_{inter_id}")
        # Turning movements (generator emits these for every intersection).
        route_ids.add(f"route_main_inbound_turn_to_N_{inter_id}")
        route_ids.add(f"route_main_inbound_turn_to_S_{inter_id}")
        route_ids.add(f"route_main_outbound_turn_to_N_{inter_id}")
        route_ids.add(f"route_main_outbound_turn_to_S_{inter_id}")
        route_ids.add(f"route_minor_N_{inter_id}_to_city")
        route_ids.add(f"route_minor_N_{inter_id}_to_atlantic")
        route_ids.add(f"route_minor_S_{inter_id}_to_city")
        route_ids.add(f"route_minor_S_{inter_id}_to_atlantic")
    route_ids.update(str(item["id"]) for item in config.get("routes", []))
    return route_ids


def _validate_time_window(item: dict[str, Any], *, context: str) -> None:
    begin = float(item.get("begin", 0))
    end = float(item.get("end", 0))
    if end <= begin:
        raise ScenarioConfigError(f"{context}: end must be greater than begin.")


def _estimated_flow_departures(flow: dict[str, Any]) -> int:
    entries = flow.get("time_profile")
    if entries:
        base_period = float(flow.get("period", 0))
        total = 0
        for entry in entries:
            begin = float(entry.get("begin", 0))
            end = float(entry.get("end", 0))
            if end <= begin:
                continue
            if "period" in entry:
                period = float(entry["period"])
            elif base_period > 0:
                scale = float(entry.get("scale", 1.0))
                period = base_period / scale if scale > 0 else 0.0
            else:
                period = 0.0
            if period > 0:
                total += math.ceil((end - begin) / period)
        return max(0, total)
    begin = float(flow.get("begin", 0))
    end = float(flow.get("end", 0))
    period = float(flow.get("period", 0))
    if end <= begin or period <= 0:
        return 0
    return max(0, math.ceil((end - begin) / period))


def _expanded_flow_count(flow: dict[str, Any]) -> int:
    entries = flow.get("time_profile")
    if isinstance(entries, list) and entries:
        return len(entries)
    return 1


def _estimated_service_departures(service: dict[str, Any], config: dict[str, Any]) -> int:
    begin = float(service.get("begin_s", config.get("simulation_begin_s", 0)))
    end = float(service.get("end_s", config.get("simulation_end_s", 7200)))
    offset = float(service.get("offset_s", 0))
    headway = float(service.get("headway_s", 0))
    first = begin + offset
    if end <= first or headway <= 0:
        return 0
    return max(0, math.ceil((end - first) / headway))
