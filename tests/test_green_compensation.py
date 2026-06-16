#!/usr/bin/env python3
"""Testes do GreenCompensationManager (v2.1, compensação NEMA-style)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.models import SignalState
from pps57_tsp.compensation import GreenCompensationManager
from pps57_tsp.config import TSPConfig, load_tsp_config
from pps57_tsp.models import TSPDecision


class _RecordingSignalControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        self.calls.append((tls_id, duration_s))


def _early_green_decision(**overrides) -> TSPDecision:
    payload = dict(
        timestamp_s=100.0,
        request_id="r1",
        vehicle_id="bus_1",
        intersection_id="I2",
        tls_id="I2",
        rsu_id="RSU",
        action="early_green",
        status="approved",
        reason="truncate",
        priority_score=0.5,
        eta_to_stopline_s=12.0,
        schedule_delay_s=60.0,
        headway_deviation_s=0.0,
        current_phase_index=3,
        current_next_switch_s=125.0,
        phase_duration_s=15.0,
    )
    payload.update(overrides)
    return TSPDecision(**payload)


def _state(tls_id: str, phase: int, *, next_switch_s, spent_s) -> SignalState:
    return SignalState(
        intersection_id=tls_id,
        tls_id=tls_id,
        rsu_id="RSU",
        timestamp_s=0.0,
        current_phase_index=phase,
        current_program_id="test",
        red_yellow_green_state="GGrr",
        next_switch_s=next_switch_s,
        spent_duration_s=spent_s,
    )


class GreenCompensationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)

    def _manager(self) -> GreenCompensationManager:
        return GreenCompensationManager(self.cits, self.tsp)

    def test_register_accumulates_green_removed(self) -> None:
        manager = self._manager()
        # remaining na decisão = 125-100 = 25s; truncado para 15s -> 10s devidos.
        manager.register_applied(_early_green_decision())
        self.assertAlmostEqual(manager.owed_s_by_tls_phase["I2"][3], 10.0)

    def test_register_is_failclosed_without_next_switch(self) -> None:
        manager = self._manager()
        manager.register_applied(_early_green_decision(current_next_switch_s=None))
        self.assertEqual(manager.owed_s_by_tls_phase, {})

    def test_pays_on_phase_entry_capped_per_cycle(self) -> None:
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_early_green_decision())  # 10s devidos à fase 3
        # Passo 1: noutra fase — só memoriza, não paga.
        results = manager.step(
            {"I2": _state("I2", 0, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        # Passo 2: entra na fase lesada (remaining 30s, spent 0.5) -> paga a
        # prestação máxima (8s), comandando 38s.
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "green_compensation")
        self.assertTrue(results[0].applied)
        self.assertAlmostEqual(results[0].parameters["granted_s"], 8.0)
        self.assertEqual(control.calls, [("I2", 38.0)])
        self.assertAlmostEqual(manager.owed_s_by_tls_phase["I2"][3], 2.0)
        # Passo 3: mesma fase, sem transição -> não paga outra vez.
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=240.0, spent_s=5.0)},
            control,
            215.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        # Ciclo seguinte: sai e volta a entrar -> paga o resto (2s).
        manager.step(
            {"I2": _state("I2", 0, next_switch_s=300.0, spent_s=1.0)},
            control,
            260.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=330.0, spent_s=0.5)},
            control,
            300.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["granted_s"], 2.0)
        self.assertAlmostEqual(manager.owed_s_by_tls_phase["I2"][3], 0.0)

    def test_no_actuation_mode_mirrors_counters_without_commands(self) -> None:
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_early_green_decision())
        manager.step(
            {"I2": _state("I2", 0, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=False,
        )
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=False,
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].applied)
        self.assertTrue(results[0].no_actuation)
        self.assertEqual(control.calls, [])
        # Contadores espelham a atuação real (coerente com H5 da safety).
        self.assertAlmostEqual(manager.owed_s_by_tls_phase["I2"][3], 2.0)

    def test_max_total_green_bounds_the_grant(self) -> None:
        # headroom = 55 - (spent 20 + remaining 34) = 1.0 -> prestação 1s.
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_early_green_decision())
        manager.step(
            {"I2": _state("I2", 0, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=244.0, spent_s=20.0)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["granted_s"], 1.0)

    def test_skips_tls_with_tsp_actuation_in_same_step(self) -> None:
        # Conflito real: o signal_state foi lido antes da atuação TSP deste
        # passo; pagar compensação com base nele reinstalaria o verde que o
        # early green acabou de cortar. O TLS intervencionado é saltado e a
        # transição pendente paga no passo seguinte.
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_early_green_decision())
        manager.step(
            {"I2": _state("I2", 0, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
            skip_tls={"I2"},
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])
        # Passo seguinte sem atuação TSP: a transição ainda conta -> paga.
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=239.0, spent_s=1.5)},
            control,
            211.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["granted_s"], 8.0)

    def test_first_tick_skip_seeds_phase_memory_and_blocks_same_phase_payback(self) -> None:
        # Codex P2 (PR #46): se a PRIMEIRA observação do TLS coincide com
        # atuação TSP (skip), a memória de fase ficava por semear; no passo
        # seguinte a MESMA fase parecia uma entrada e a compensação pagava na
        # fase que o early green acabou de truncar, anulando a intervenção.
        manager = self._manager()
        control = _RecordingSignalControl()
        # Early green aplicado no primeiro passo de simulação: trunca a fase 3
        # (deixa 10s devidos) e o TLS é saltado nesse mesmo passo.
        manager.register_applied(_early_green_decision())
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=115.0, spent_s=10.0)},
            control,
            100.0,
            apply_actuation=True,
            skip_tls={"I2"},
        )
        self.assertEqual(results, [])
        # Passo seguinte, ainda na fase truncada: NÃO é uma entrada de fase ->
        # nada a pagar (senão re-instalava o verde cortado).
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=114.0, spent_s=11.0)},
            control,
            101.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])
        # A dívida só paga na PRÓXIMA activação real da fase 3.
        manager.step(
            {"I2": _state("I2", 0, next_switch_s=150.0, spent_s=1.0)},
            control,
            120.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=180.0, spent_s=0.5)},
            control,
            150.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["granted_s"], 8.0)

    def test_disabled_config_is_a_noop(self) -> None:
        manager = GreenCompensationManager(self.cits, TSPConfig(root=ROOT, raw={}))
        control = _RecordingSignalControl()
        manager.register_applied(_early_green_decision())
        self.assertEqual(manager.owed_s_by_tls_phase, {})
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])


def _extension_decision(**overrides) -> TSPDecision:
    payload = dict(
        timestamp_s=100.0,
        request_id="r2",
        vehicle_id="bus_1",
        intersection_id="I2",
        tls_id="I2",
        rsu_id="RSU",
        action="green_extension",
        status="approved",
        reason="extend",
        priority_score=0.5,
        eta_to_stopline_s=12.0,
        schedule_delay_s=60.0,
        headway_deviation_s=0.0,
        current_phase_index=0,
        current_next_switch_s=110.0,
        extension_s=9.0,
    )
    payload.update(overrides)
    return TSPDecision(**payload)


class CoordinationRecoveryTestCase(unittest.TestCase):
    """v2.2: reclaim do verde estendido para re-alinhar o ciclo (opt-in)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)

    def _manager(self, **actuation_overrides) -> GreenCompensationManager:
        actuation = {
            **self.tsp.actuation,
            "coordination_recovery_enabled": True,
            **actuation_overrides,
        }
        tsp = TSPConfig(root=ROOT, raw={**self.tsp.raw, "actuation": actuation})
        return GreenCompensationManager(self.cits, tsp)

    def test_register_accumulates_extension_reclaim(self) -> None:
        manager = self._manager()
        manager.register_applied(_extension_decision())
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][0], 9.0)

    def test_disabled_by_default_keeps_v21_behaviour(self) -> None:
        # Sem o flag, extensões não registam reclaim — byte-idêntico ao v2.1.
        manager = GreenCompensationManager(self.cits, self.tsp)
        manager.register_applied(_extension_decision())
        self.assertEqual(manager.reclaim_s_by_tls_phase, {})

    def test_reclaims_on_phase_entry_capped_per_cycle(self) -> None:
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_extension_decision())  # 9s a reclamar à fase 0
        # Passo 1: noutra fase — só memoriza.
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        # Passo 2: entra na fase estendida (remaining 30, spent 0.5) -> encurta
        # a prestação máxima (8s), comandando 22s.
        results = manager.step(
            {"I2": _state("I2", 0, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "coordination_recovery")
        self.assertAlmostEqual(results[0].parameters["reclaimed_s"], 8.0)
        self.assertEqual(control.calls, [("I2", 22.0)])
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][0], 1.0)
        # Ciclo seguinte: paga o resto (1s).
        manager.step(
            {"I2": _state("I2", 3, next_switch_s=300.0, spent_s=1.0)},
            control,
            260.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 0, next_switch_s=330.0, spent_s=0.5)},
            control,
            300.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["reclaimed_s"], 1.0)
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][0], 0.0)
        self.assertAlmostEqual(manager.reclaimed_s_total, 9.0)

    def test_reclaim_never_shortens_below_min_green(self) -> None:
        # remaining 9, spent 0.5, min_green 8 -> headroom = 9 - 7.5 = 1.5s.
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_extension_decision())
        manager.step(
            {"I2": _state("I2", 3, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 0, next_switch_s=219.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["reclaimed_s"], 1.5)
        # spent + novo remaining = 0.5 + 7.5 = 8.0 = min_green_s exato.
        self.assertEqual(control.calls, [("I2", 7.5)])

    def test_reclaim_is_failclosed_without_min_green(self) -> None:
        from copy import deepcopy

        cits = deepcopy(self.cits)
        del cits.raw["safety_constraints"]["min_green_s"]
        actuation = {**self.tsp.actuation, "coordination_recovery_enabled": True}
        tsp = TSPConfig(root=ROOT, raw={**self.tsp.raw, "actuation": actuation})
        manager = GreenCompensationManager(cits, tsp)
        control = _RecordingSignalControl()
        manager.register_applied(_extension_decision())
        manager.step(
            {"I2": _state("I2", 3, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 0, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])
        # O reclaim fica pendente (não é descartado), mas nunca comanda.
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][0], 9.0)

    def test_compensation_grant_takes_precedence_over_reclaim_same_phase(self) -> None:
        # Fase 3 com dívida de compensação (10s) E reclaim (9s): na entrada
        # paga-se a compensação; o reclaim espera pela ativação seguinte —
        # nunca dois comandos absolutos no mesmo passo.
        manager = self._manager()
        control = _RecordingSignalControl()
        manager.register_applied(_early_green_decision())  # owed: fase 3, 10s
        manager.register_applied(_extension_decision(current_phase_index=3))  # reclaim: fase 3, 9s
        manager.step(
            {"I2": _state("I2", 0, next_switch_s=210.0, spent_s=5.0)},
            control,
            200.0,
            apply_actuation=True,
        )
        results = manager.step(
            {"I2": _state("I2", 3, next_switch_s=240.0, spent_s=0.5)},
            control,
            210.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "green_compensation")
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][3], 9.0)

    def test_reduce_reclaim_clamps_at_zero(self) -> None:
        manager = self._manager()
        manager.register_applied(_extension_decision())
        manager.reduce_reclaim("I2", 0, 4.0)
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][0], 5.0)
        manager.reduce_reclaim("I2", 0, 100.0)
        self.assertAlmostEqual(manager.reclaim_s_by_tls_phase["I2"][0], 0.0)


if __name__ == "__main__":
    unittest.main()
