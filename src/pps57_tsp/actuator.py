#!/usr/bin/env python3
"""Atuador TSP para SUMO/TraCI."""

from __future__ import annotations

from dataclasses import dataclass

from pps57_cits.models import SignalState

from .models import ActuationResult, DecisionStatus, TSPAction, TSPDecision
from .signal_control import SignalControlAdapter


@dataclass
class TraciTSPActuator:
    adapter: SignalControlAdapter
    apply_actuation: bool = True

    def apply(
        self, decision: TSPDecision, signal_state: SignalState, sim_time_s: float
    ) -> ActuationResult:
        if decision.status != DecisionStatus.APPROVED.value or not decision.requires_actuation:
            return ActuationResult(
                decision_id=decision.decision_id,
                timestamp_s=sim_time_s,
                tls_id=decision.tls_id,
                action=decision.action,
                applied=False,
                no_actuation=not self.apply_actuation,
                command="none",
                reason="decision_not_actuable_or_not_approved",
            )

        command, parameters = _command_for_decision(decision, signal_state, sim_time_s)
        if not self.apply_actuation:
            return ActuationResult(
                decision_id=decision.decision_id,
                timestamp_s=sim_time_s,
                tls_id=decision.tls_id,
                action=decision.action,
                applied=False,
                no_actuation=True,
                command=command,
                reason="sumo_no_actuation_flag_would_apply",
                parameters=parameters,
            )

        try:
            controller_validation = _controller_validation(
                self.adapter,
                decision,
                signal_state,
                sim_time_s,
                command,
                parameters,
            )
            if not controller_validation.accepted:
                return ActuationResult(
                    decision_id=decision.decision_id,
                    timestamp_s=sim_time_s,
                    tls_id=decision.tls_id,
                    action=decision.action,
                    applied=False,
                    no_actuation=False,
                    command=command,
                    reason=controller_validation.reason,
                    parameters=parameters,
                    controller_response=controller_validation.to_dict(),
                    severity=controller_validation.severity,
                )
            parameters = dict(controller_validation.adjusted_parameters)
            if decision.action == TSPAction.GREEN_EXTENSION.value:
                new_duration_s = float(parameters["new_phase_duration_s"])
                self.adapter.set_phase_duration(decision.tls_id, new_duration_s)
            elif decision.action == TSPAction.EARLY_GREEN.value:
                phase_duration_s = float(parameters["phase_duration_s"])
                self.adapter.set_phase_duration(decision.tls_id, phase_duration_s)
            else:
                return ActuationResult(
                    decision_id=decision.decision_id,
                    timestamp_s=sim_time_s,
                    tls_id=decision.tls_id,
                    action=decision.action,
                    applied=False,
                    no_actuation=False,
                    command="none",
                    reason="unsupported_action_for_traci_actuator",
                    parameters=parameters,
                    severity="warning",
                )
        except Exception as exc:  # SUMO/TraCI may raise runtime-specific errors.
            # `severity=error` é o gancho estruturado para auditoria: o TLS pode
            # ter ficado num estado intermédio (setPhaseDuration parcial), e o
            # controlador deve impor cooldown e logar com proeminência. Não
            # confiar em parsing de substrings de `reason`.
            return ActuationResult(
                decision_id=decision.decision_id,
                timestamp_s=sim_time_s,
                tls_id=decision.tls_id,
                action=decision.action,
                applied=False,
                no_actuation=False,
                command=command,
                reason=f"traci_actuation_error:{exc}",
                parameters=parameters,
                severity="error",
            )

        return ActuationResult(
            decision_id=decision.decision_id,
            timestamp_s=sim_time_s,
            tls_id=decision.tls_id,
            action=decision.action,
            applied=True,
            no_actuation=False,
            command=command,
            reason="applied_safe_tsp_action_via_traci",
            parameters=parameters,
            controller_response=controller_validation.to_dict(),
        )


def _command_for_decision(
    decision: TSPDecision, signal_state: SignalState, sim_time_s: float
) -> tuple[str, dict]:
    if decision.action == TSPAction.GREEN_EXTENSION.value:
        remaining_s = 0.0
        if signal_state.next_switch_s is not None:
            remaining_s = max(0.0, float(signal_state.next_switch_s) - sim_time_s)
        new_duration_s = remaining_s + float(decision.extension_s)
        return "trafficlight.setPhaseDuration", {
            "remaining_phase_s": round(remaining_s, 3),
            "extension_s": round(float(decision.extension_s), 3),
            "new_phase_duration_s": round(new_duration_s, 3),
        }
    if decision.action == TSPAction.EARLY_GREEN.value:
        return "trafficlight.setPhaseDuration", {
            "phase_duration_s": round(float(decision.phase_duration_s or 2.0), 3),
            "target_phase_index": decision.target_phase_index,
            "implementation": "red_truncation_not_direct_phase_jump",
        }
    return "none", {}


def _controller_validation(
    adapter: SignalControlAdapter,
    decision: TSPDecision,
    signal_state: SignalState,
    sim_time_s: float,
    command: str,
    parameters: dict,
):
    if hasattr(adapter, "validate_actuation"):
        return adapter.validate_actuation(decision, signal_state, sim_time_s, command, parameters)
    from .signal_control import ControllerCommandValidation

    return ControllerCommandValidation(
        accepted=True,
        reason="adapter_without_controller_validation",
        effective_at_s=sim_time_s,
        adjusted_parameters=dict(parameters),
    )
