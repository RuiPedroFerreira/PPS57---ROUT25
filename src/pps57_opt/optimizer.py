#!/usr/bin/env python3
"""Deterministic offline policy optimization with a mandatory safety filter."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Dict, Iterable, List, Optional

from pps57_cits.config import CITSConfig
from pps57_tsp.config import TSPConfig
from pps57_tsp.action_planner import decision_for_action
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import DecisionStatus, TSPAction, TSPDecision
from pps57_tsp.safety import TSPSafetyLayer

from .config import OptimizationConfig
from .event_dataset import load_event_training_scenarios
from .models import CandidateEvaluation, LearnedPolicyRule, OfflineScenario
from .state import state_bucket_for_context


@dataclass
class OfflineOptimizationController:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    optimization_config: OptimizationConfig
    scenarios: Optional[List[OfflineScenario]] = None

    def __post_init__(self) -> None:
        self.engine = TSPDecisionEngine(self.cits_config, self.tsp_config)

    def run(self) -> Dict[str, object]:
        scenarios = self.scenarios or self._load_event_scenarios()
        all_candidates: List[CandidateEvaluation] = []
        selected: List[CandidateEvaluation] = []
        policy_rules: Dict[str, LearnedPolicyRule] = {}

        for scenario in scenarios:
            candidates = self._evaluate_scenario(scenario)
            chosen = self._select_candidate(candidates)
            for item in candidates:
                item.selected = item is chosen
            all_candidates.extend(candidates)
            selected.append(chosen)
            current_rule = policy_rules.get(chosen.state_bucket)
            if current_rule is None or chosen.reward > current_rule.reward:
                policy_rules[chosen.state_bucket] = LearnedPolicyRule(
                    state_bucket=chosen.state_bucket,
                    action=chosen.action,
                    reward=chosen.reward,
                    source_scenario_id=chosen.scenario_id,
                    safety_status=chosen.safety_status,
                    safety_reason=chosen.safety_reason,
                )

        self._write_outputs(scenarios, all_candidates, selected, list(policy_rules.values()))
        return self._summary(scenarios, all_candidates, selected, list(policy_rules.values()))

    def _load_event_scenarios(self) -> List[OfflineScenario]:
        path = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("event_training_dataset", "outputs/event_training_dataset.jsonl")
        )
        scenarios = load_event_training_scenarios(path, self.tsp_config.actuating_actions())
        if not scenarios:
            raise ValueError(
                "No SUMO/TraCI event training scenarios found. Run a TSP SUMO execution and "
                "scripts/build_event_training_dataset.py before policy optimization."
            )
        return scenarios

    def _evaluate_scenario(self, scenario: OfflineScenario) -> List[CandidateEvaluation]:
        baseline = self.engine.decide(scenario.request, scenario.signal_state, scenario.sim_time_s)
        baseline_candidate = self._evaluate_candidate(
            scenario,
            policy_id="baseline_tsp_decision_engine",
            decision=baseline,
        )

        candidates = [baseline_candidate]
        for action in self.optimization_config.offline_training.get("candidate_actions", []):
            decision = self._candidate_decision(action, baseline)
            candidates.append(
                self._evaluate_candidate(
                    scenario,
                    policy_id="offline_candidate",
                    decision=decision,
                )
            )
        return candidates

    def _candidate_decision(self, action: str, baseline: TSPDecision) -> TSPDecision:
        if action == TSPAction.NO_ACTION.value:
            reason = "offline_candidate_no_action"
        elif action == TSPAction.GREEN_EXTENSION.value:
            reason = "offline_candidate_green_extension"
        elif action == TSPAction.EARLY_GREEN.value:
            reason = "offline_candidate_early_green"
        elif action == TSPAction.REEVALUATE_NEXT_CYCLE.value:
            reason = "offline_candidate_reevaluate_next_cycle"
        elif action == TSPAction.REJECT.value:
            reason = "offline_candidate_reject"
        else:
            reason = f"offline_candidate_unsupported:{action}"

        return decision_for_action(
            self.tsp_config,
            action=action,
            baseline=baseline,
            reason=reason,
            notes=[f"Offline policy candidate: {action}."],
        )

    def _evaluate_candidate(self, scenario: OfflineScenario, *, policy_id: str, decision: TSPDecision) -> CandidateEvaluation:
        safety = TSPSafetyLayer(self.cits_config, self.tsp_config)
        if bool(self.optimization_config.offline_training.get("signal_program_verified", False)):
            safety.set_signal_program_verified(True)
        # M7: aplicar estado inicial opcional do cenário (cooldown ativo,
        # intervenções consecutivas) para exercitar caminhos com estado da
        # Safety Layer. Sem isto a optimização offline nunca testa cooldown.
        if scenario.initial_last_intervention_time_by_tls:
            safety.last_intervention_time_by_tls.update(scenario.initial_last_intervention_time_by_tls)
        if scenario.initial_consecutive_interventions_by_tls:
            safety.consecutive_interventions_by_tls.update(scenario.initial_consecutive_interventions_by_tls)
        validation = safety.validate(decision, scenario.signal_state, scenario.sim_time_s)
        safe_decision = validation.safe_decision
        reward = self._reward(scenario, safe_decision, validation.status)
        return CandidateEvaluation(
            scenario_id=scenario.scenario_id,
            state_bucket=self._state_bucket(scenario),
            policy_id=policy_id,
            action=safe_decision.action,
            reward=round(reward, 4),
            safety_status=validation.status,
            safety_reason=validation.reason,
            selected=False,
            safe_decision=safe_decision,
            notes=validation.notes,
        )

    def _select_candidate(self, candidates: List[CandidateEvaluation]) -> CandidateEvaluation:
        allowed = [item for item in candidates if not item.is_safety_blocked]
        if not allowed:
            # Todos os candidatos foram bloqueados pela Safety Layer: devolve o
            # baseline (candidates[0]) já na sua forma safe/bloqueada.
            return candidates[0]
        # argmax sobre candidatos seguros. O baseline está incluído em
        # `allowed` sempre que ele próprio é seguro, logo o escolhido nunca tem
        # reward inferior ao baseline — não é preciso um fallback explícito
        # (o antigo branch baseline_fallback_margin era inalcançável).
        return max(allowed, key=lambda item: item.reward)

    def _reward(self, scenario: OfflineScenario, decision: TSPDecision, safety_status: str) -> float:
        reward_cfg = self.optimization_config.reward
        if safety_status == DecisionStatus.BLOCKED_BY_SAFETY.value:
            return -float(reward_cfg.get("unsafe_candidate_penalty", 1000))

        request = scenario.request
        min_score = float(self.tsp_config.decision_policy.get("min_priority_score", 0.35))
        if decision.priority_score < min_score and decision.requires_actuation:
            return -float(reward_cfg.get("reject_penalty", 20.0)) * 2

        remaining = TSPDecisionEngine.remaining_phase_time_s(scenario.signal_state, scenario.sim_time_s)
        remaining_s = float(remaining or 0.0)
        required_green_s = request.eta_to_stopline_s + float(self.tsp_config.decision_policy.get("eta_arrival_buffer_s", 4))
        delay_component = (
            float(reward_cfg.get("bus_delay_weight", 1.0))
            * request.schedule_delay_s
            / float(reward_cfg.get("schedule_delay_divisor", 10.0))
        )
        headway_component = (
            float(reward_cfg.get("headway_weight", 0.25))
            * abs(request.headway_deviation_s)
            / float(reward_cfg.get("headway_divisor", 10.0))
        )
        proximity_component = float(reward_cfg.get("proximity_weight", 0.3)) * max(
            0.0, float(reward_cfg.get("proximity_eta_baseline_s", 45.0)) - request.eta_to_stopline_s
        )
        benefit = delay_component + headway_component + proximity_component
        traffic_penalty = float(reward_cfg.get("general_traffic_penalty_per_second", 0.35))
        traffic_penalty *= self._traffic_pressure_multiplier(scenario)

        if decision.action == TSPAction.GREEN_EXTENSION.value:
            needed = max(0.0, required_green_s - remaining_s)
            if needed <= 0:
                return -decision.extension_s * traffic_penalty
            overserve_penalty = max(0.0, decision.extension_s - needed) * traffic_penalty
            return benefit - decision.extension_s * traffic_penalty - overserve_penalty

        if decision.action == TSPAction.EARLY_GREEN.value:
            min_eta = float(self.tsp_config.decision_policy.get("early_green_min_eta_s", 10))
            if request.eta_to_stopline_s < min_eta:
                return -float(reward_cfg.get("reevaluate_penalty", 8.0)) * 2
            truncation = float(decision.phase_duration_s or 0.0)
            return (
                benefit
                - truncation * traffic_penalty
                - float(reward_cfg.get("early_green_truncation_penalty", 2.0))
            )

        if decision.action == TSPAction.NO_ACTION.value:
            target_phase = self._target_phase(decision)
            enough_green = (
                target_phase is not None
                and scenario.signal_state.current_phase_index == target_phase
                and remaining_s >= required_green_s
            )
            return (
                benefit + float(reward_cfg.get("no_action_sufficient_green_bonus", 5.0))
                if enough_green
                else -benefit * float(reward_cfg.get("no_action_insufficient_green_factor", 0.8))
            )

        if decision.action == TSPAction.REEVALUATE_NEXT_CYCLE.value:
            min_eta = float(self.tsp_config.decision_policy.get("early_green_min_eta_s", 10))
            if request.eta_to_stopline_s < min_eta:
                return float(reward_cfg.get("reevaluate_below_min_eta_bonus", 6.0))
            return -float(reward_cfg.get("reevaluate_penalty", 8.0))

        if decision.action == TSPAction.REJECT.value:
            if decision.priority_score < min_score:
                return float(reward_cfg.get("reject_below_min_score_bonus", 6.0))
            return -float(reward_cfg.get("reject_penalty", 20.0)) - request.schedule_delay_s / float(
                reward_cfg.get("reject_schedule_delay_divisor", 20.0)
            )

        return float(reward_cfg.get("unsupported_action_fallback", -50.0))

    def _target_phase(self, decision: TSPDecision) -> int | None:
        if decision.target_phase_index is not None:
            return decision.target_phase_index
        mapping = self.tsp_config.phase_mapping_for_movement(decision.priority_movement_id, decision.tls_id)
        raw = mapping.get("target_phase_index")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _state_bucket(self, scenario: OfflineScenario) -> str:
        return state_bucket_for_context(
            self.tsp_config,
            self.optimization_config.offline_training.get("state_buckets", {}),
            scenario.request,
            scenario.signal_state,
            scenario.sim_time_s,
            active_request_count=scenario.active_request_count,
            queue_vehicle_count=scenario.queue_vehicle_count,
            halted_vehicle_count=scenario.halted_vehicle_count,
            mean_speed_mps=scenario.mean_speed_mps,
            waiting_time_s=scenario.waiting_time_s,
            occupancy=scenario.occupancy,
            spillback_risk=scenario.spillback_risk,
            seconds_since_last_intervention_s=scenario.seconds_since_last_intervention_s,
        )

    def _traffic_pressure_multiplier(self, scenario: OfflineScenario) -> float:
        reward_cfg = self.optimization_config.reward
        state_cfg = self.optimization_config.offline_training.get("state_buckets", {})
        high_queue = int(state_cfg.get("high_queue_vehicle_count", 8))
        high_occupancy = float(state_cfg.get("high_occupancy", 0.6))
        high_active_requests = int(state_cfg.get("high_active_requests", 2))
        if (
            scenario.queue_vehicle_count >= high_queue
            or scenario.halted_vehicle_count >= high_queue
            or scenario.occupancy >= high_occupancy
            or scenario.active_request_count >= high_active_requests
            or scenario.spillback_risk
        ):
            return float(reward_cfg.get("traffic_pressure_penalty_multiplier", 8.0))
        return 1.0

    def _write_outputs(
        self,
        scenarios: Iterable[OfflineScenario],
        candidates: Iterable[CandidateEvaluation],
        selected: Iterable[CandidateEvaluation],
        rules: Iterable[LearnedPolicyRule],
    ) -> None:
        sample_log = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("sample_log", "outputs/offline_policy_samples.jsonl")
        )
        candidate_log = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("candidate_log", "outputs/policy_candidates.jsonl")
        )
        policy_report = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("policy_report", "reports/policy_report.json")
        )
        summary_report = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("summary_report", "reports/policy_optimization_summary.json")
        )
        for path in [sample_log, candidate_log, policy_report, summary_report]:
            path.parent.mkdir(parents=True, exist_ok=True)

        sample_log.write_text(
            "\n".join(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) for item in scenarios) + "\n",
            encoding="utf-8",
        )
        candidate_log.write_text("\n".join(item.to_json() for item in candidates) + "\n", encoding="utf-8")
        policy_report.write_text(
            json.dumps(
                {
                    "policy_id": "offline_safe_policy_comparison",
                    "baseline_policy": "baseline_tsp_decision_engine",
                    "methodology": "deterministic_argmax_over_event_derived_sumo_traci_scenarios",
                    "is_reinforcement_learning": False,
                    "safety_filter_required": True,
                    "rules": [item.to_dict() for item in rules],
                    "selected_decisions": [item.to_dict() for item in selected],
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _summary(
        self,
        scenarios: List[OfflineScenario],
        candidates: List[CandidateEvaluation],
        selected: List[CandidateEvaluation],
        rules: List[LearnedPolicyRule],
    ) -> Dict[str, object]:
        baseline_candidates = [item for item in candidates if item.policy_id == "baseline_tsp_decision_engine"]
        unsafe_filtered = [item for item in candidates if item.is_safety_blocked]
        selected_by_action: Dict[str, int] = {}
        baseline_by_action: Dict[str, int] = {}
        for item in selected:
            selected_by_action[item.action] = selected_by_action.get(item.action, 0) + 1
        for item in baseline_candidates:
            baseline_by_action[item.action] = baseline_by_action.get(item.action, 0) + 1

        baseline_reward = round(sum(item.reward for item in baseline_candidates), 4)
        optimized_reward = round(sum(item.reward for item in selected), 4)

        # Métrica não-tautológica: o argmax sobre candidatos seguros (que incluem
        # o baseline) garante optimized_reward >= baseline_reward por construção,
        # logo reward_delta não prova superioridade. O que é informativo é em
        # quantos cenários a política escolhida difere do baseline e quantos
        # baselines eram inseguros.
        baseline_by_scenario = {item.scenario_id: item for item in baseline_candidates}
        action_changes = 0
        baseline_unsafe = 0
        for chosen in selected:
            base = baseline_by_scenario.get(chosen.scenario_id)
            if base is None:
                continue
            if base.is_safety_blocked:
                baseline_unsafe += 1
            if chosen.action != base.action:
                action_changes += 1

        # P4 observability: quão frequentemente o shield bloqueou a ação proposta,
        # repartido por ação proposta e por regra de segurança (a ação safe de um
        # candidato bloqueado mantém a proposta, logo .action == ação proposta). O
        # NOVO sinal são as repartições (by_proposed_action / by_safety_reason) e a
        # taxa; override_count == unsafe_candidates_filtered por construção, mantido
        # para o sub-dict ser auto-contido e auditável.
        blocked = [item for item in candidates if item.is_safety_blocked]
        shield_overrides = {
            "override_count": len(blocked),
            "override_rate": round(len(blocked) / len(candidates), 4) if candidates else 0.0,
            "by_proposed_action": dict(sorted(Counter(item.action for item in blocked).items())),
            "by_safety_reason": dict(sorted(Counter(item.safety_reason for item in blocked).items())),
        }

        summary = {
            "component_id": self.optimization_config.raw.get("component_id"),
            "version": self.optimization_config.raw.get("version"),
            "scenario_id": self.optimization_config.raw.get("scenario_id"),
            "mode": "offline-policy-comparison",
            "methodology": "deterministic_argmax_over_event_derived_sumo_traci_scenarios",
            "is_reinforcement_learning": False,
            "scenario_count": len(scenarios),
            "candidate_count": len(candidates),
            "unsafe_candidates_filtered": len(unsafe_filtered),
            "safety_filter_required": bool(self.optimization_config.safety.get("mandatory_filter", True)),
            "baseline_policy": "baseline_tsp_decision_engine",
            "optimized_policy": "offline_safe_policy_comparison",
            "baseline_reward": baseline_reward,
            "optimized_reward": optimized_reward,
            "reward_delta": round(optimized_reward - baseline_reward, 4),
            "reward_delta_is_nonnegative_by_construction": True,
            "reward_delta_caveat": (
                "argmax sobre candidatos seguros inclui o baseline => reward_delta >= 0 "
                "por construção; NÃO demonstra superioridade. Ver "
                "optimized_action_changes_vs_baseline."
            ),
            "baseline_unsafe_scenarios": baseline_unsafe,
            "shield_overrides": shield_overrides,
            "optimized_action_changes_vs_baseline": action_changes,
            "optimized_action_unchanged_vs_baseline": len(selected) - action_changes,
            "baseline_by_action": baseline_by_action,
            "selected_by_action": selected_by_action,
            "learned_rule_count": len(rules),
            "outputs": {
                "sample_log": str(self.optimization_config.path_from_root(self.optimization_config.logging.get("sample_log"))),
                "candidate_log": str(self.optimization_config.path_from_root(self.optimization_config.logging.get("candidate_log"))),
                "policy_report": str(self.optimization_config.path_from_root(self.optimization_config.logging.get("policy_report"))),
                "summary_report": str(self.optimization_config.path_from_root(self.optimization_config.logging.get("summary_report"))),
            },
        }
        summary_path = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("summary_report", "reports/policy_optimization_summary.json")
        )
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return summary
