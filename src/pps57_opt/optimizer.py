#!/usr/bin/env python3
"""Treino offline tabular com Safety Layer obrigatória."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Iterable, List

from pps57_cits.config import CITSConfig
from pps57_cits.messages import RequestedManeuver
from pps57_cits.models import SignalState
from pps57_tsp.config import TSPConfig
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import DecisionStatus, TSPAction, TSPDecision
from pps57_tsp.safety import TSPSafetyLayer

from .config import OptimizationConfig
from .dataset import build_offline_scenarios
from .models import CandidateEvaluation, LearnedPolicyRule, OfflineScenario


@dataclass
class OfflineOptimizationController:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    optimization_config: OptimizationConfig

    def __post_init__(self) -> None:
        self.engine = TSPDecisionEngine(self.cits_config, self.tsp_config)

    def run(self) -> Dict[str, object]:
        scenarios = build_offline_scenarios(self.cits_config)
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

    def _evaluate_scenario(self, scenario: OfflineScenario) -> List[CandidateEvaluation]:
        baseline = self.engine.decide(scenario.request, scenario.signal_state, scenario.sim_time_s)
        baseline_candidate = self._evaluate_candidate(
            scenario,
            policy_id="baseline_tsp_pacote4",
            decision=baseline,
        )

        candidates = [baseline_candidate]
        for action in self.optimization_config.offline_training.get("candidate_actions", []):
            decision = self._candidate_decision(action, baseline, scenario.signal_state)
            candidates.append(
                self._evaluate_candidate(
                    scenario,
                    policy_id="offline_candidate",
                    decision=decision,
                )
            )
        return candidates

    def _candidate_decision(self, action: str, baseline: TSPDecision, signal_state: SignalState) -> TSPDecision:
        policy = self.tsp_config.decision_policy
        mapping = self.tsp_config.phase_mapping_for_tls(baseline.tls_id)
        target_phase = _optional_int(mapping.get("corridor_green_phase_index"))

        if action == TSPAction.GREEN_EXTENSION.value:
            extension_s = baseline.extension_s if baseline.extension_s > 0 else float(policy.get("green_extension_default_s", 8))
            return baseline.copy_with(
                action=action,
                status=DecisionStatus.PROPOSED.value,
                reason="offline_candidate_green_extension",
                extension_s=extension_s,
                phase_duration_s=None,
                target_phase_index=None,
                notes=["Pacote 5 candidato offline: green extension."],
            )

        if action == TSPAction.EARLY_GREEN.value:
            return baseline.copy_with(
                action=action,
                status=DecisionStatus.PROPOSED.value,
                reason="offline_candidate_early_green",
                extension_s=0.0,
                phase_duration_s=float(policy.get("red_truncation_to_s", 2)),
                target_phase_index=target_phase,
                notes=["Pacote 5 candidato offline: early green."],
            )

        if action == TSPAction.NO_ACTION.value:
            reason = "offline_candidate_no_action"
        elif action == TSPAction.REEVALUATE_NEXT_CYCLE.value:
            reason = "offline_candidate_reevaluate_next_cycle"
        elif action == TSPAction.REJECT.value:
            reason = "offline_candidate_reject"
        else:
            reason = f"offline_candidate_unsupported:{action}"

        return baseline.copy_with(
            action=action,
            status=DecisionStatus.PROPOSED.value,
            reason=reason,
            extension_s=0.0,
            phase_duration_s=None,
            target_phase_index=None,
            notes=[f"Pacote 5 candidato offline: {action}."],
        )

    def _evaluate_candidate(self, scenario: OfflineScenario, *, policy_id: str, decision: TSPDecision) -> CandidateEvaluation:
        safety = TSPSafetyLayer(self.cits_config, self.tsp_config)
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
            return candidates[0]

        baseline = candidates[0]
        best = max(allowed, key=lambda item: item.reward)
        margin = float(self.optimization_config.reward.get("baseline_fallback_margin", 0.0))
        if best.reward + margin < baseline.reward and not baseline.is_safety_blocked:
            return baseline
        return best

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
        delay_component = float(reward_cfg.get("bus_delay_weight", 1.0)) * request.schedule_delay_s / 10.0
        headway_component = float(reward_cfg.get("headway_weight", 0.25)) * abs(request.headway_deviation_s) / 10.0
        proximity_component = float(reward_cfg.get("proximity_weight", 0.3)) * max(0.0, 45.0 - request.eta_to_stopline_s)
        benefit = delay_component + headway_component + proximity_component
        traffic_penalty = float(reward_cfg.get("general_traffic_penalty_per_second", 0.35))

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
            return benefit - truncation * traffic_penalty - 2.0

        if decision.action == TSPAction.NO_ACTION.value:
            corridor_phase = self._corridor_phase(decision.tls_id)
            enough_green = (
                corridor_phase is not None
                and scenario.signal_state.current_phase_index == corridor_phase
                and remaining_s >= required_green_s
            )
            return benefit + 5.0 if enough_green else -benefit * 0.8

        if decision.action == TSPAction.REEVALUATE_NEXT_CYCLE.value:
            min_eta = float(self.tsp_config.decision_policy.get("early_green_min_eta_s", 10))
            if request.eta_to_stopline_s < min_eta:
                return 6.0
            return -float(reward_cfg.get("reevaluate_penalty", 8.0))

        if decision.action == TSPAction.REJECT.value:
            if decision.priority_score < min_score:
                return 6.0
            return -float(reward_cfg.get("reject_penalty", 20.0)) - request.schedule_delay_s / 20.0

        return -50.0

    def _corridor_phase(self, tls_id: str) -> int | None:
        mapping = self.tsp_config.phase_mapping_for_tls(tls_id)
        return _optional_int(mapping.get("corridor_green_phase_index"))

    def _state_bucket(self, scenario: OfflineScenario) -> str:
        buckets = self.optimization_config.offline_training.get("state_buckets", {})
        eta_close = float(buckets.get("eta_close_s", 10))
        eta_far = float(buckets.get("eta_far_s", 25))
        high_delay = float(buckets.get("high_delay_s", 90))
        switch_close = float(buckets.get("phase_switch_close_s", 5))
        request = scenario.request
        state = scenario.signal_state
        remaining = TSPDecisionEngine.remaining_phase_time_s(state, scenario.sim_time_s)

        eta_bucket = "eta_close" if request.eta_to_stopline_s <= eta_close else "eta_mid"
        if request.eta_to_stopline_s >= eta_far:
            eta_bucket = "eta_far"
        delay_bucket = "delay_high" if request.schedule_delay_s >= high_delay else "delay_low"
        corridor_phase = self._corridor_phase(state.tls_id)
        phase_bucket = "phase_unknown"
        if state.red_yellow_green_state and "y" in state.red_yellow_green_state.lower():
            phase_bucket = "yellow"
        elif corridor_phase is not None and state.current_phase_index == corridor_phase:
            phase_bucket = "corridor_green"
        elif state.current_phase_index is not None:
            phase_bucket = "corridor_red"
        switch_bucket = "switch_close" if remaining is not None and remaining <= switch_close else "switch_open"
        return "|".join([phase_bucket, eta_bucket, delay_bucket, switch_bucket])

    def _write_outputs(
        self,
        scenarios: Iterable[OfflineScenario],
        candidates: Iterable[CandidateEvaluation],
        selected: Iterable[CandidateEvaluation],
        rules: Iterable[LearnedPolicyRule],
    ) -> None:
        sample_log = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("sample_log", "outputs/pacote5_offline_samples.jsonl")
        )
        candidate_log = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("candidate_log", "outputs/pacote5_policy_candidates.jsonl")
        )
        policy_report = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("policy_report", "reports/pacote5_policy_report.json")
        )
        summary_report = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("summary_report", "reports/pacote5_optimization_summary.json")
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
                    "policy_id": "pacote5_offline_safe_policy_comparison",
                    "baseline_policy": "baseline_tsp_pacote4",
                    "methodology": "deterministic_argmax_over_fixed_candidate_actions_per_synthetic_scenario",
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
        baseline_candidates = [item for item in candidates if item.policy_id == "baseline_tsp_pacote4"]
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

        summary = {
            "package_id": self.optimization_config.raw.get("package_id"),
            "version": self.optimization_config.raw.get("version"),
            "scenario_id": self.optimization_config.raw.get("scenario_id"),
            "mode": "offline-policy-comparison",
            "methodology": "deterministic_argmax_over_fixed_candidate_actions_per_synthetic_scenario",
            "is_reinforcement_learning": False,
            "scenario_count": len(scenarios),
            "candidate_count": len(candidates),
            "unsafe_candidates_filtered": len(unsafe_filtered),
            "safety_filter_required": bool(self.optimization_config.safety.get("mandatory_filter", True)),
            "baseline_policy": "baseline_tsp_pacote4",
            "optimized_policy": "pacote5_offline_safe_policy_comparison",
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
            self.optimization_config.logging.get("summary_report", "reports/pacote5_optimization_summary.json")
        )
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return summary


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
