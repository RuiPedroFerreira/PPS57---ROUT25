#!/usr/bin/env python3
"""Árbitro de corredor (P6): consciência cross-TLS no loop de decisão.

Corre DENTRO do controller, depois do sort network-wide e ANTES da Safety Layer,
e só pode **DESCER** uma decisão para NOT_ACTUABLE (nunca aprova/eleva). A Safety
Layer continua a ser o portão final fail-closed: tudo o que o árbitro PERMITE
passa à validate(); tudo o que ele DEFERE salta a atuação (como a supressão de
mesmo-passo), pelo que um pedido deferido nunca chega a APPROVED.

Não é um "tier acima da interseção" nem re-otimiza offsets/green-wave em runtime.
Faz três coisas, todas OPT-IN por config (bloco `corridor` no tsp_config); com o
bloco ausente é um no-op completo e o comportamento é byte-idêntico:

1. cap OPCIONAL de recovery-debt de corredor (soma o debt por-TLS da Safety Layer);
2. defer quando o TLS a jusante já está em spillback_risk (respect_downstream_spillback);
3. flag (nota informativa, sem deferir) de green-wave watch quando o jusante está
   congestionado mas o respeito a spillback está desligado (flag_green_wave).

Adjacência a jusante: derivada da convenção de nomes de edge fromNode_toNode via
o índice edge_to_intersection do CITSConfig (estático, sem solver).
"""

from __future__ import annotations

from dataclasses import dataclass

from pps57_cits.config import CITSConfig
from pps57_cits.models import NetworkStateSnapshot

from .config import TSPConfig
from .models import ReasonCode, TSPDecision
from .util import optional_float as _optional_float


@dataclass(frozen=True)
class ArbiterOutcome:
    """Resultado do árbitro para um pedido.

    allow=False => o controller desce a decisão para NOT_ACTUABLE com reason_code.
    note (quando allow=True) => nota informativa a anexar (flag, não altera ação).
    """

    allow: bool
    reason_code: str | None = None
    note: str | None = None


_ALLOW = ArbiterOutcome(allow=True)


@dataclass
class CorridorArbiter:
    cits_config: CITSConfig
    tsp_config: TSPConfig

    def arbitrate(
        self,
        decision: TSPDecision,
        *,
        recovery_debt_by_tls: dict[str, float] | None = None,
        network_states: dict[str, NetworkStateSnapshot] | None = None,
    ) -> ArbiterOutcome:
        corridor = self.tsp_config.raw.get("corridor", {})
        if not isinstance(corridor, dict) or not corridor:
            return _ALLOW  # bloco ausente -> no-op completo (comportamento inalterado)

        # 1. Cap opcional de recovery-debt de corredor (soft, optimization — NÃO
        # fail-closed): ausência da chave => desligado, como _optional_safety_value.
        # Cap <= 0 (ou null/inválido) => desligado: um orçamento de 0s ainda
        # permite a primeira intervenção (evita o footgun de 0 deferir tudo).
        max_debt = _optional_float(corridor.get("max_corridor_recovery_debt_s"))
        if max_debt is not None and max_debt > 0.0:
            total_debt = sum(max(0.0, float(v)) for v in (recovery_debt_by_tls or {}).values())
            if total_debt >= max_debt:
                return ArbiterOutcome(
                    allow=False,
                    reason_code=ReasonCode.DEFERRED_CORRIDOR_RECOVERY_DEBT_EXHAUSTED.value,
                )

        # 2/3. Spillback a jusante.
        downstream_tls = self._downstream_tls(decision)
        downstream = (
            network_states.get(downstream_tls) if (network_states and downstream_tls) else None
        )
        if downstream is not None and downstream.spillback_risk:
            if bool(corridor.get("respect_downstream_spillback", False)):
                return ArbiterOutcome(
                    allow=False,
                    reason_code=ReasonCode.DEFERRED_DOWNSTREAM_SPILLBACK_RISK.value,
                )
            if bool(corridor.get("flag_green_wave", False)):
                return ArbiterOutcome(
                    allow=True,
                    note=f"corridor_green_wave_watch:downstream={downstream_tls}_spillback_risk",
                )
        return _ALLOW

    def _downstream_tls(self, decision: TSPDecision) -> str | None:
        """TLS a jusante via a edge seguinte (fromNode_toNode -> approach do jusante)."""
        next_edge = getattr(decision, "next_edge_id", "") or ""
        if not next_edge:
            return None
        intersection = self.cits_config.edge_to_intersection.get(next_edge)
        if intersection is None or intersection.tls_id == decision.tls_id:
            return None
        return intersection.tls_id
