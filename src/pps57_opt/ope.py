#!/usr/bin/env python3
"""Off-policy evaluation (OPE) over the SUMO/TraCI event corpus.

Porquê
------
O resumo do optimizer reporta `reward_delta`, mas o argmax-sobre-candidatos-
seguros inclui o baseline, logo `reward_delta >= 0` por construção — é
tautológico e NÃO prova que a política é melhor. Este módulo dá uma estimativa
**falsificável** do valor de uma política-alvo a partir do que foi de facto
registado (a "behavior policy"), em vez de reavaliar a função de reward.

Honestidade / limites
---------------------
OPE rigoroso precisa de (a) a ação tomada pela behavior policy, (b) o outcome
realizado por decisão e (c) a propensão da behavior policy. O corpus regista
(a); (b) só existe se o event row trouxer `realized_outcome` (hoje ausente ->
veredicto honesto `inconclusive_without_outcomes`); (c) NÃO é registado, pelo
que assumimos a behavior policy determinística (propensão 1.0) e sinalizamo-lo
em `assumed_deterministic_behavior_propensity`. Sob essa assunção o estimador
IPS de uma política-alvo determinística reduz-se à média dos outcomes nos
cenários onde alvo == behavior, escalada pela cobertura — reportamos cobertura,
IC (t de Student) e um veredicto que degrada para `limited_support` quando a
sobreposição de suporte é baixa. Nunca recomputa reward no loop vivo.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import statistics
from typing import Callable, Dict, List, Optional, Tuple

from pps57_sumo.stats import mean_ci95

from .models import OfflineScenario

# Uma política-alvo é uma função cenário -> ação proposta (ou None se a política
# não tem regra para aquele estado). Mantém o OPE desacoplado de RuntimePolicy.
TargetActionOf = Callable[[OfflineScenario], Optional[str]]


@dataclass(frozen=True)
class OPEReport:
    method: str
    verdict: str
    n_scenarios: int
    n_eligible: int
    n_matched: int
    coverage: float
    estimate: Optional[float]
    matched_mean_outcome: Optional[float]
    confidence_interval: Optional[Tuple[Optional[float], Optional[float]]]
    assumed_deterministic_behavior_propensity: bool
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        ci = data.get("confidence_interval")
        data["confidence_interval"] = list(ci) if ci is not None else None
        return data


# Veredictos honestos (espelham o padrão inconclusive_* do outcome_evaluator).
INCONCLUSIVE_NO_OUTCOMES = "inconclusive_without_outcomes"
INCONCLUSIVE_NO_BEHAVIOR = "inconclusive_without_behavior_actions"
LIMITED_SUPPORT = "limited_support"
ESTIMATED = "estimated"


def evaluate_policy(
    scenarios: List[OfflineScenario],
    target_action_of: TargetActionOf,
    *,
    min_coverage: float = 0.2,
) -> OPEReport:
    """Estima o valor de uma política-alvo por IPS (propensão determinística).

    Devolve um OPEReport com veredicto honesto: sem outcomes realizados ->
    `inconclusive_without_outcomes`; sem ações da behavior policy ->
    `inconclusive_without_behavior_actions`; cobertura < min_coverage ->
    `limited_support`; caso contrário `estimated` com IC 95%.
    """
    n = len(scenarios)
    with_outcome = [s for s in scenarios if s.realized_outcome is not None]
    if not with_outcome:
        return OPEReport(
            method="ips_assumed_deterministic_behavior",
            verdict=INCONCLUSIVE_NO_OUTCOMES,
            n_scenarios=n,
            n_eligible=0,
            n_matched=0,
            coverage=0.0,
            estimate=None,
            matched_mean_outcome=None,
            confidence_interval=None,
            assumed_deterministic_behavior_propensity=True,
            notes=[
                "Nenhum cenário tem realized_outcome; o corpus SUMO atual não regista "
                "outcome por-decisão. OPE não pode estimar valor — falsificável apenas "
                "quando outcomes forem registados.",
            ],
        )

    eligible = [s for s in with_outcome if s.behavior_policy_action is not None]
    if not eligible:
        return OPEReport(
            method="ips_assumed_deterministic_behavior",
            verdict=INCONCLUSIVE_NO_BEHAVIOR,
            n_scenarios=n,
            n_eligible=0,
            n_matched=0,
            coverage=0.0,
            estimate=None,
            matched_mean_outcome=None,
            confidence_interval=None,
            assumed_deterministic_behavior_propensity=True,
            notes=["Há outcomes mas nenhuma behavior_policy_action registada; sem suporte para IPS."],
        )

    # IPS com propensão da behavior assumida = 1.0 (determinística): peso = 1 se a
    # ação-alvo coincide com a behavior, 0 caso contrário. term = peso * outcome.
    ips_terms: List[float] = []
    matched_outcomes: List[float] = []
    for scenario in eligible:
        target_action = target_action_of(scenario)
        outcome = float(scenario.realized_outcome)  # not None by construction
        if target_action is not None and target_action == scenario.behavior_policy_action:
            ips_terms.append(outcome)
            matched_outcomes.append(outcome)
        else:
            ips_terms.append(0.0)

    n_eligible = len(eligible)
    n_matched = len(matched_outcomes)
    coverage = n_matched / n_eligible if n_eligible else 0.0
    base_notes = [
        "Propensão da behavior policy assumida determinística (1.0): não há propensões "
        "registadas, logo o IPS não corrige por sobre/sub-amostragem.",
        f"Cobertura de suporte (alvo==behavior) = {n_matched}/{n_eligible}.",
    ]

    if n_matched == 0:
        # Sem sobreposição de suporte: o IPS daria 0.0 mecanicamente, mas isso não
        # é um valor medido -> não estimável (honesto, distinto de um zero medido).
        return OPEReport(
            method="ips_assumed_deterministic_behavior",
            verdict=LIMITED_SUPPORT,
            n_scenarios=n,
            n_eligible=n_eligible,
            n_matched=0,
            coverage=0.0,
            estimate=None,
            matched_mean_outcome=None,
            confidence_interval=None,
            assumed_deterministic_behavior_propensity=True,
            notes=base_notes
            + ["Sem sobreposição de suporte (a política-alvo nunca coincide com a behavior); IPS não estimável."],
        )

    ips = mean_ci95(ips_terms)
    estimate = ips["mean"]
    matched_mean_outcome = round(statistics.fmean(matched_outcomes), 3)

    if n_eligible < 2:
        # Uma única observação elegível não limita variância -> não fabricar um IC
        # de largura zero; reportar a estimativa pontual mas sem IC e como limitada.
        return OPEReport(
            method="ips_assumed_deterministic_behavior",
            verdict=LIMITED_SUPPORT,
            n_scenarios=n,
            n_eligible=n_eligible,
            n_matched=n_matched,
            coverage=round(coverage, 4),
            estimate=estimate,
            matched_mean_outcome=matched_mean_outcome,
            confidence_interval=None,
            assumed_deterministic_behavior_propensity=True,
            notes=base_notes
            + ["Amostra elegível única: IC indefinido (uma observação não limita a variância)."],
        )

    verdict = ESTIMATED if coverage >= min_coverage else LIMITED_SUPPORT
    notes = list(base_notes)
    if verdict == LIMITED_SUPPORT:
        notes.append(
            f"Cobertura {coverage:.2f} < min_coverage {min_coverage:.2f}: estimativa de "
            "baixo suporte, tratar como indicativa, não conclusiva."
        )
    return OPEReport(
        method="ips_assumed_deterministic_behavior",
        verdict=verdict,
        n_scenarios=n,
        n_eligible=n_eligible,
        n_matched=n_matched,
        coverage=round(coverage, 4),
        estimate=estimate,
        matched_mean_outcome=matched_mean_outcome,
        confidence_interval=(ips["ci95_low"], ips["ci95_high"]),
        assumed_deterministic_behavior_propensity=True,
        notes=notes,
    )
