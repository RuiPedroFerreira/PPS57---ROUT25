"""Vehicle classification helpers shared by SUMO KPI parsers."""

from __future__ import annotations

# B37: lower-case-with-underscore form. Matching is case-insensitive (so BUS_/Bus_
# also match) and requires the underscore boundary so "business_car" does NOT match.
DEFAULT_BUS_ID_PREFIXES = ("bus_",)
DEFAULT_BUS_TYPE_NAMES = {"stcp_bus", "transit_bus"}


def is_bus_like(
    vehicle_id: str,
    vehicle_type: str,
    *,
    bus_id_prefixes: tuple[str, ...] = DEFAULT_BUS_ID_PREFIXES,
    bus_type_names: set[str] | frozenset[str] = DEFAULT_BUS_TYPE_NAMES,
) -> bool:
    vehicle_id_lc = vehicle_id.lower()
    vehicle_type_lc = vehicle_type.lower()
    id_prefixes = tuple(prefix.lower() for prefix in bus_id_prefixes)
    # B37: the type check requires a "bus" token (== "bus" or a "bus_"/"bus-" prefix)
    # rather than any "bus*" prefix, so "business_car" is no longer misclassified.
    return (
        vehicle_id_lc.startswith(id_prefixes)
        or vehicle_type_lc == "bus"
        or vehicle_type_lc.startswith(("bus_", "bus-"))
        or vehicle_type_lc in bus_type_names
    )
