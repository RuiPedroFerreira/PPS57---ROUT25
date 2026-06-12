#!/usr/bin/env python3
"""Testes do PriorityEventManager (v2.2, lifecycle check-in/check-out)."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.messages import synth_srem
from pps57_cits.models import SignalState
from pps57_tsp.compensation import GreenCompensationManager
from pps57_tsp.config import TSPConfig, load_tsp_config
from pps57_tsp.events import PriorityEventManager
from pps57_tsp.models import TSPDecision
from pps57_tsp.request_store import PriorityRequestState, PriorityRequestStore
from pps57_tsp.safety import TSPSafetyLayer


class _RecordingSignalControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        self.calls.append((tls_id, duration_s))


def _extension_decision(**overrides) -> TSPDecision:
    payload = dict(
        timestamp_s=100.0,
        request_id="r1",
        vehicle_id="bus_1",
        intersection_id="I2",
        tls_id="I2",
        rsu_id="RSU",
        action="green_extension",
        status="approved",
        reason="extend",
        priority_score=0.5,
        eta_to_stopline_s=16.0,
        schedule_delay_s=60.0,
        headway_deviation_s=0.0,
        current_phase_index=0,
        current_next_switch_s=110.0,
        extension_s=4.0,
    )
    payload.update(overrides)
    return TSPDecision(**payload)


def _state(tls_id: str = "I2", phase: int = 0, *, next_switch_s, spent_s=5.0) -> SignalState:
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


def _cleared_store(vehicle_id: str = "bus_1", tls_id: str = "I2", status: str = "cleared") -> PriorityRequestStore:
    request = synth_srem(
        sim_time_s=100.0,
        vehicle_id=vehicle_id,
        intersection_alias=tls_id,
        tls_id=tls_id,
        rsu_id="RSU",
        lane_id="I1_I2_0",
    )
    store = PriorityRequestStore()
    store.states_by_key[f"{vehicle_id}:{tls_id}"] = PriorityRequestState(
        request=request, first_seen_s=100.0, last_seen_s=100.0, status=status
    )
    return store


class PriorityEventManagerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.base_tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        cls.tsp = TSPConfig(
            root=ROOT,
            raw={
                **cls.base_tsp.raw,
                "actuation": {
                    **cls.base_tsp.actuation,
                    "priority_event_lifecycle_enabled": True,
                    "coordination_recovery_enabled": True,
                },
            },
        )

    def _manager(self) -> PriorityEventManager:
        return PriorityEventManager(self.cits, self.tsp)

    def _wired(self):
        """Manager + safety + compensation + controlo gravador, prontos a step()."""
        manager = self._manager()
        safety = TSPSafetyLayer(self.cits, self.tsp)
        compensation = GreenCompensationManager(self.cits, self.tsp)
        control = _RecordingSignalControl()
        return manager, safety, compensation, control

    def test_disabled_by_default_is_a_noop(self) -> None:
        manager = PriorityEventManager(self.cits, self.base_tsp)
        manager.register_applied(_extension_decision())
        self.assertEqual(manager.events_by_key, {})
        self.assertIsNone(manager.active_event("I2", "bus_1", 0))

    def test_open_then_accumulate_installments(self) -> None:
        manager = self._manager()
        manager.register_applied(_extension_decision())
        manager.register_applied(_extension_decision(extension_s=3.0))
        event = manager.active_event("I2", "bus_1", 0)
        self.assertIsNotNone(event)
        self.assertAlmostEqual(event.granted_total_s, 7.0)
        self.assertAlmostEqual(event.original_end_s, 110.0)  # fixado no 1.º grant
        self.assertEqual(manager.opened_count, 1)
        # Fase diferente não autoriza continuações.
        self.assertIsNone(manager.active_event("I2", "bus_1", 3))

    def test_open_is_failclosed_without_next_switch(self) -> None:
        manager = self._manager()
        manager.register_applied(_extension_decision(current_next_switch_s=None))
        self.assertEqual(manager.events_by_key, {})

    def test_checkout_returns_unused_green_and_settles_ledgers(self) -> None:
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())  # original_end=110, +4s
        safety.recovery_debt_by_tls["I2"] = 4.0
        compensation.reclaim_s_by_tls_phase["I2"] = {0: 4.0}
        # t=105: fase prolongada até 114; o fim original era 110 -> devolve 4s.
        results = manager.step(
            {"I2": _state(next_switch_s=114.0)},
            _cleared_store(),
            control,
            safety,
            compensation,
            105.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "extension_checkout_return")
        self.assertAlmostEqual(results[0].parameters["returned_s"], 4.0)
        self.assertEqual(control.calls, [("I2", 5.0)])  # repõe o fim original
        self.assertAlmostEqual(safety.recovery_debt_by_tls["I2"], 0.0)
        self.assertAlmostEqual(compensation.reclaim_s_by_tls_phase["I2"][0], 0.0)
        self.assertEqual(manager.events_by_key, {})
        self.assertEqual(manager.checkout_termination_count, 1)

    def test_checkout_never_shortens_below_original_end(self) -> None:
        # Bus saiu DEPOIS do fim original (110): restored_remaining = 0 — a
        # fase termina já, exactamente como o plano base teria feito; o verde
        # devolvido é só o excedente ainda não consumido.
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())
        results = manager.step(
            {"I2": _state(next_switch_s=114.0)},
            _cleared_store(),
            control,
            safety,
            compensation,
            112.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["returned_s"], 2.0)
        self.assertEqual(control.calls, [("I2", 0.0)])

    def test_immaterial_return_closes_event_without_command(self) -> None:
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())
        results = manager.step(
            {"I2": _state(next_switch_s=110.5)},
            _cleared_store(),
            control,
            safety,
            compensation,
            105.0,  # devolução = 5.5 - 5.0 = 0.5s < 1s
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])
        self.assertEqual(manager.events_by_key, {})

    def test_phase_change_closes_event_naturally(self) -> None:
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())
        results = manager.step(
            {"I2": _state(phase=3, next_switch_s=140.0)},
            _cleared_store(status="active"),
            control,
            safety,
            compensation,
            115.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])
        self.assertEqual(manager.events_by_key, {})
        self.assertEqual(manager.closed_natural_count, 1)

    def test_active_bus_keeps_event_open(self) -> None:
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())
        results = manager.step(
            {"I2": _state(next_switch_s=114.0)},
            _cleared_store(status="active"),
            control,
            safety,
            compensation,
            105.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertIsNotNone(manager.active_event("I2", "bus_1", 0))

    def test_skip_tls_defers_checkout_to_next_step(self) -> None:
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())
        results = manager.step(
            {"I2": _state(next_switch_s=114.0)},
            _cleared_store(),
            control,
            safety,
            compensation,
            105.0,
            apply_actuation=True,
            skip_tls={"I2"},
        )
        self.assertEqual(results, [])
        self.assertEqual(control.calls, [])
        self.assertIsNotNone(manager.active_event("I2", "bus_1", 0))
        # Passo seguinte sem comandos concorrentes: termina e devolve.
        results = manager.step(
            {"I2": _state(next_switch_s=113.0)},
            _cleared_store(),
            control,
            safety,
            compensation,
            106.0,
            apply_actuation=True,
        )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].parameters["returned_s"], 3.0)

    def test_degraded_next_switch_defers_checkout(self) -> None:
        manager, safety, compensation, control = self._wired()
        manager.register_applied(_extension_decision())
        results = manager.step(
            {"I2": _state(next_switch_s=None)},
            _cleared_store(),
            control,
            safety,
            compensation,
            105.0,
            apply_actuation=True,
        )
        self.assertEqual(results, [])
        self.assertIsNotNone(manager.active_event("I2", "bus_1", 0))


if __name__ == "__main__":
    unittest.main()
