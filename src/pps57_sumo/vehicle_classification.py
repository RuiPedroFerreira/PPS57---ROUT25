"""Vehicle classification helpers shared by SUMO KPI parsers."""

from __future__ import annotations

DEFAULT_BUS_ID_PREFIXES = ("bus_", "Bus")
DEFAULT_BUS_TYPE_NAMES = {"stcp_bus", "transit_bus"}


def is_bus_like(
    vehicle_id: str,
    vehicle_type: str,
    *,
    bus_id_prefixes: tuple[str, ...] = DEFAULT_BUS_ID_PREFIXES,
    bus_type_names: set[str] | frozenset[str] = DEFAULT_BUS_TYPE_NAMES,
) -> bool:
    vehicle_type_lc = vehicle_type.lower()
    return (
        vehicle_id.startswith(bus_id_prefixes)
        or vehicle_type_lc.startswith("bus")
        or vehicle_type_lc in bus_type_names
    )
