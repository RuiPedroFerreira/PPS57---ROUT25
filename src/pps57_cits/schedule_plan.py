#!/usr/bin/env python3
"""Stand-in determinístico de aderência ao horário (schedule adherence / headway).

Porquê
------
Hoje `VehicleObservation.schedule_delay_s` e `headway_deviation_s` nunca são
preenchidos pelo adaptador TraCI (ficam em 0.0), pelo que a OBU recai num proxy
de waiting-time e os caminhos de prioridade HIGH_DELAY/HEADWAY_RECOVERY quase
nunca disparam por motivos reais de horário. Numa pilha real estes valores
chegariam via AVL/APC. Como esta plataforma é **SUMO-only** (sem dados de
campo), este módulo é o **stand-in determinístico** desse feed: produz desvios
plausíveis, reprodutíveis e ancorados ao horário configurado.

Honestidade
-----------
Isto **não mede** aderência real a partir de eventos de paragem do SUMO (o
config só declara *headways*, não horas absolutas por paragem, e a interface é
sem estado). É um desvio sintético **determinístico**: o mesmo
`(seed, line_code, vehicle_id, edge)` produz sempre o mesmo valor, protegendo a
reprodutibilidade multi-seed dos testes. Fica **desligado por defeito**
(`schedule_plan.enabled=false`); ligá-lo é uma experiência explícita que
desloca KPIs.

Produz apenas campos de domínio (não formatos wire GTFS-RT/AVL); qualquer
serialização wire pertenceria a uma subclasse de ProtocolCodec no futuro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Tuple

if TYPE_CHECKING:  # evita custo/ciclo em runtime; só para type-checkers
    from .config import CITSConfig
    from .models import VehicleObservation

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulePlanProvider:
    """Fonte injetável de (schedule_delay_s, headway_deviation_s) por veículo.

    `services_by_line_code` mapeia o `line_code` do horário (ex.:
    "STCP500_PROXY_W", que é também o `line_id` que o TraCI devolve) para o
    dict do serviço em `public_transport.services`.
    """

    services_by_line_code: Dict[str, Dict] = field(default_factory=dict)
    seed: int = 57
    schedule_delay_scale_s: float = 90.0
    headway_deviation_fraction: float = 0.25

    @classmethod
    def from_config(cls, cits_config: "CITSConfig") -> Optional["SchedulePlanProvider"]:
        """Constrói o provider a partir do bloco `schedule_plan`; None se desligado.

        Lê o horário do ficheiro `timetable_source` (default
        configs/sumo_scenario_base.json) e os defaults de escala de
        `obu_policy`, mantendo tudo config-driven.
        """
        block = cits_config.schedule_plan
        if not isinstance(block, dict) or not bool(block.get("enabled", False)):
            return None
        source = str(block.get("timetable_source", "configs/sumo_scenario_base.json"))
        try:
            raw = json.loads(Path(cits_config.path_from_root(source)).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _LOGGER.warning(
                "schedule_plan enabled but timetable_source %r could not be loaded (%s); provider disabled.",
                source,
                exc,
            )
            return None
        public_transport = raw.get("public_transport") if isinstance(raw, dict) else None
        services = public_transport.get("services") if isinstance(public_transport, dict) else None
        services_by_line_code: Dict[str, Dict] = {}
        for service in services if isinstance(services, list) else []:
            if isinstance(service, dict) and service.get("line_code"):
                services_by_line_code[str(service["line_code"])] = service
        if not services_by_line_code:
            _LOGGER.warning(
                "schedule_plan enabled but timetable_source %r yielded no services with a line_code; "
                "provider disabled.",
                source,
            )
            return None
        obu_policy = cits_config.obu_policy
        # Default da escala de atraso ancorado ao threshold de elegibilidade da
        # OBU (1.5x => alguns veículos cruzam o limiar e exercitam HIGH_DELAY).
        default_delay_scale = _coerce_float(obu_policy.get("delay_threshold_s"), 60.0) * 1.5
        # Coerção defensiva: um escalar malformado recai no default em vez de
        # rebentar a construção do adaptador (from_config corre em
        # TraciSimulationAdapter.__init__, fora de qualquer try). Escala e
        # fração clampadas a >= 0 para honrar o contrato de atraso não-negativo.
        return cls(
            services_by_line_code=services_by_line_code,
            seed=_coerce_int(block.get("random_seed"), 57),
            schedule_delay_scale_s=max(
                0.0, _coerce_float(block.get("schedule_delay_scale_s"), default_delay_scale)
            ),
            headway_deviation_fraction=max(
                0.0, _coerce_float(block.get("headway_deviation_fraction"), 0.25)
            ),
        )

    def schedule_adherence_for(
        self, observation: "VehicleObservation", sim_time_s: float
    ) -> Optional[Tuple[float, float]]:
        """Devolve (schedule_delay_s, headway_deviation_s) ou None.

        None quando a linha não está no horário (a OBU recai então no proxy).
        Determinístico por `(seed, line_code, vehicle_id, edge)`: estável
        durante a aproximação a uma interseção, varia ao longo do corredor.
        O headway_deviation_s é ancorado ao headway agendado activo; a magnitude
        de schedule_delay_s é escalada por config (schedule_delay_scale_s), não
        derivada de horas por paragem (que o config não declara).
        """
        service = self.services_by_line_code.get(observation.line_id)
        if service is None:
            return None
        scheduled_headway_s = self._scheduled_headway_s(service, sim_time_s)
        # Atraso só não-negativo (um veículo está "atrasado" N s); o desvio de
        # headway é ±. Unidades determinísticas em [0,1] / [-1,1].
        delay_unit = self._unit(
            observation.line_id, observation.vehicle_id, observation.edge_id, "delay"
        )
        headway_unit = (
            2.0
            * self._unit(
                observation.line_id, observation.vehicle_id, observation.edge_id, "headway"
            )
            - 1.0
        )
        schedule_delay_s = round(delay_unit * self.schedule_delay_scale_s, 3)
        headway_deviation_s = round(
            headway_unit * self.headway_deviation_fraction * scheduled_headway_s, 3
        )
        return schedule_delay_s, headway_deviation_s

    @staticmethod
    def _scheduled_headway_s(service: Dict, sim_time_s: float) -> float:
        """Headway agendado activo em sim_time_s (lê o headway_schedule por janela)."""
        windows = service.get("headway_schedule", [])
        if isinstance(windows, list):
            for window in windows:
                if not isinstance(window, dict):
                    continue
                begin_s = float(window.get("begin_s", 0.0))
                end_s = float(window.get("end_s", 0.0))
                if begin_s <= sim_time_s < end_s:
                    return float(window.get("headway_s", service.get("headway_s", 600.0)))
        return float(service.get("headway_s", 600.0))

    def _unit(self, *parts: object) -> float:
        """Hash determinístico estável -> [0, 1] (sem aleatoriedade nem relógio).

        O limite superior é fechado: por arredondamento IEEE-754 o rácio pode
        dar exactamente 1.0 (prob. ~2^-54), o que não viola nenhum bound a
        jusante (a escala/fração apenas atingem o seu máximo).
        """
        key = "|".join([str(self.seed)] + [str(part) for part in parts])
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
