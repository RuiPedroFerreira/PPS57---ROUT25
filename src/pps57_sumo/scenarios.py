#!/usr/bin/env python3
"""Scenario profile loading and validation for PPS57 SUMO runs."""
from __future__ import annotations

from copy import deepcopy
import math
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
        if key in {"description", "justification", "kpi_focus", "expected_behaviour", "tags"}
    }
    if "simulation_end_s" in profile:
        config["simulation_end_s"] = profile["simulation_end_s"]
    if "random_seed" in profile:
        config["random_seed"] = profile["random_seed"]

    demand_profile = str(profile.get("demand_profile", config.get("active_demand_profile", "am_peak")))
    config["active_demand_profile"] = demand_profile
    config["demand_profiles"] = resolve_demand_profiles(config.get("demand_profiles", {}))

    _apply_public_transport_overrides(config, profile)
    if "events" in profile:
        config["events"] = deepcopy(profile["events"])

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
        "flow_count": len(flows),
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

    stop_ids = {str(stop["id"]) for stop in config.get("public_transport", {}).get("stops", [])}
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
        if "begin" in profile:
            flow["begin"] = profile["begin"]
        if "end" in profile:
            flow["end"] = profile["end"]
        if flow_id in flow_period_override:
            flow["period"] = flow_period_override[flow_id]
        if flow_id in flow_updates:
            for key, value in flow_updates[flow_id].items():
                if key != "id":
                    flow[key] = deepcopy(value)
        flows.append(flow)

    for flow in profile.get("additional_flows", []):
        flows.append(deepcopy(flow))
    return flows


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
        route_ids.add(f"route_cross_NS_{inter['id']}")
        route_ids.add(f"route_cross_SN_{inter['id']}")
    route_ids.update(str(item["id"]) for item in config.get("routes", []))
    return route_ids


def _validate_time_window(item: dict[str, Any], *, context: str) -> None:
    begin = float(item.get("begin", 0))
    end = float(item.get("end", 0))
    if end <= begin:
        raise ScenarioConfigError(f"{context}: end must be greater than begin.")


def _estimated_flow_departures(flow: dict[str, Any]) -> int:
    begin = float(flow.get("begin", 0))
    end = float(flow.get("end", 0))
    period = float(flow.get("period", 0))
    if end <= begin or period <= 0:
        return 0
    return max(0, math.ceil((end - begin) / period))


def _estimated_service_departures(service: dict[str, Any], config: dict[str, Any]) -> int:
    begin = float(service.get("begin_s", config.get("simulation_begin_s", 0)))
    end = float(service.get("end_s", config.get("simulation_end_s", 7200)))
    offset = float(service.get("offset_s", 0))
    headway = float(service.get("headway_s", 0))
    first = begin + offset
    if end <= first or headway <= 0:
        return 0
    return max(0, math.ceil((end - first) / headway))
