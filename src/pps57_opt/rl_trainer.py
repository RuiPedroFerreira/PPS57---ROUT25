#!/usr/bin/env python3
"""Tabular Q-learning trainer for simulated TSP policy scenarios."""
from __future__ import annotations

from dataclasses import dataclass
import json
import random
from typing import Dict, List, Tuple

from pps57_cits.config import CITSConfig
from pps57_tsp.config import TSPConfig

from .config import OptimizationConfig
from .dataset import build_offline_scenarios
from .models import CandidateEvaluation, LearnedPolicyRule, OfflineScenario
from .optimizer import OfflineOptimizationController


@dataclass
class TabularQLearningController:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    optimization_config: OptimizationConfig

    def __post_init__(self) -> None:
        self.optimizer = OfflineOptimizationController(
            self.cits_config,
            self.tsp_config,
            self.optimization_config,
        )

    def run(self) -> Dict[str, object]:
        scenarios = build_offline_scenarios(self.cits_config)
        action_space = list(self.optimization_config.offline_training.get("candidate_actions", []))
        if not action_space:
            raise ValueError("No candidate actions configured for RL training.")

        cfg = self.optimization_config.reinforcement_learning
        episodes = int(cfg.get("episodes", 200))
        alpha = float(cfg.get("alpha", 0.25))
        gamma = float(cfg.get("gamma", 0.0))
        epsilon = float(cfg.get("epsilon_start", 0.35))
        epsilon_min = float(cfg.get("epsilon_min", 0.02))
        epsilon_decay = float(cfg.get("epsilon_decay", 0.985))
        rng = random.Random(int(cfg.get("seed", 57)))

        q_values: Dict[Tuple[str, str], float] = {}
        visits: Dict[Tuple[str, str], int] = {}
        candidate_cache = {scenario.scenario_id: self.optimizer._evaluate_scenario(scenario) for scenario in scenarios}
        scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}

        for _episode in range(max(1, episodes)):
            order = list(scenarios)
            rng.shuffle(order)
            for scenario in order:
                candidates = candidate_cache[scenario.scenario_id]
                by_action = {candidate.action: candidate for candidate in candidates}
                state = self.optimizer._state_bucket(scenario)
                safe_actions = [action for action in action_space if action in by_action and not by_action[action].is_safety_blocked]
                if not safe_actions:
                    continue
                if rng.random() < epsilon:
                    action = rng.choice(safe_actions)
                else:
                    action = max(safe_actions, key=lambda item: q_values.get((state, item), 0.0))
                reward = by_action[action].reward
                old_q = q_values.get((state, action), 0.0)
                next_best = max((q_values.get((state, item), 0.0) for item in safe_actions), default=0.0)
                q_values[(state, action)] = old_q + alpha * (reward + gamma * next_best - old_q)
                visits[(state, action)] = visits.get((state, action), 0) + 1
            epsilon = max(epsilon_min, epsilon * epsilon_decay)

        learned_rules = self._rules_from_q_values(q_values, visits, candidate_cache, scenario_by_id)
        self._write_outputs(q_values, visits, learned_rules, episodes, epsilon)
        return self._summary(q_values, visits, learned_rules, episodes, epsilon)

    def _rules_from_q_values(
        self,
        q_values: Dict[Tuple[str, str], float],
        visits: Dict[Tuple[str, str], int],
        candidate_cache: Dict[str, List[CandidateEvaluation]],
        scenario_by_id: Dict[str, OfflineScenario],
    ) -> List[LearnedPolicyRule]:
        by_state: Dict[str, list[Tuple[str, float]]] = {}
        for (state, action), value in q_values.items():
            if visits.get((state, action), 0) <= 0:
                continue
            by_state.setdefault(state, []).append((action, value))

        rules: List[LearnedPolicyRule] = []
        for state, actions in sorted(by_state.items()):
            action, value = max(actions, key=lambda item: item[1])
            source_scenario_id = self._source_scenario_for_state(state, scenario_by_id)
            safety_status = ""
            safety_reason = ""
            for candidate in candidate_cache.get(source_scenario_id, []):
                if candidate.action == action:
                    safety_status = candidate.safety_status
                    safety_reason = candidate.safety_reason
                    break
            rules.append(
                LearnedPolicyRule(
                    state_bucket=state,
                    action=action,
                    reward=round(value, 4),
                    source_scenario_id=source_scenario_id,
                    safety_status=safety_status,
                    safety_reason=safety_reason,
                )
            )
        return rules

    def _source_scenario_for_state(self, state: str, scenarios: Dict[str, OfflineScenario]) -> str:
        for scenario in scenarios.values():
            if self.optimizer._state_bucket(scenario) == state:
                return scenario.scenario_id
        return ""

    def _write_outputs(
        self,
        q_values: Dict[Tuple[str, str], float],
        visits: Dict[Tuple[str, str], int],
        rules: List[LearnedPolicyRule],
        episodes: int,
        final_epsilon: float,
    ) -> None:
        q_table_report = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("q_table_report", "reports/tabular_q_policy_report.json")
        )
        summary_report = self.optimization_config.path_from_root(
            self.optimization_config.logging.get("rl_training_summary", "reports/rl_training_summary.json")
        )
        for path in [q_table_report, summary_report]:
            path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "state_bucket": state,
                "action": action,
                "q_value": round(value, 4),
                "visits": visits.get((state, action), 0),
            }
            for (state, action), value in sorted(q_values.items())
        ]
        q_table_report.write_text(
            json.dumps(
                {
                    "policy_id": "tabular_q_learning_policy",
                    "algorithm": "tabular_q_learning",
                    "is_reinforcement_learning": True,
                    "training_environment": "simulated_offline_scenarios",
                    "safety_filter_required": True,
                    "rules": [rule.to_dict() for rule in rules],
                    "q_table": rows,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        summary_report.write_text(
            json.dumps(self._summary(q_values, visits, rules, episodes, final_epsilon), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def _summary(
        self,
        q_values: Dict[Tuple[str, str], float],
        visits: Dict[Tuple[str, str], int],
        rules: List[LearnedPolicyRule],
        episodes: int,
        final_epsilon: float,
    ) -> Dict[str, object]:
        return {
            "component_id": self.optimization_config.raw.get("component_id"),
            "mode": "tabular-q-learning",
            "algorithm": "tabular_q_learning",
            "is_reinforcement_learning": True,
            "training_environment": "simulated_offline_scenarios",
            "online_learning_in_production": False,
            "episodes": episodes,
            "state_action_count": len(q_values),
            "visited_state_action_count": sum(1 for value in visits.values() if value > 0),
            "learned_rule_count": len(rules),
            "final_epsilon": round(final_epsilon, 4),
            "safety_filter_required": bool(self.optimization_config.safety.get("mandatory_filter", True)),
            "policy_report": str(
                self.optimization_config.path_from_root(
                    self.optimization_config.logging.get("q_table_report", "reports/tabular_q_policy_report.json")
                )
            ),
        }
