#!/usr/bin/env python3
"""P6 corridor arbiter: opt-in, pre-Safety, downgrade-only cross-TLS arbitration."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.models import NetworkStateSnapshot
from pps57_tsp.config import TSPConfig, load_tsp_config
from pps57_tsp.corridor_arbiter import CorridorArbiter
from pps57_tsp.models import ReasonCode, TSPDecision

CITS = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
# "I1_I2" is a controlled approach edge of I2 -> downstream TLS of a bus leaving
# I1 onto I1_I2 is I2 (verified against configs/cits_v2x_config.json).
DOWNSTREAM_EDGE = "I1_I2"
DOWNSTREAM_TLS = "I2"


def tsp_with_corridor(**corridor) -> TSPConfig:
    return TSPConfig(root=ROOT, raw={"corridor": corridor} if corridor else {})


def decision(tls_id: str = "I1", next_edge_id: str = DOWNSTREAM_EDGE, action: str = "green_extension") -> TSPDecision:
    return TSPDecision(
        timestamp_s=0.0,
        request_id="r1",
        vehicle_id="bus_1",
        intersection_id=tls_id,
        tls_id=tls_id,
        rsu_id="RSU",
        action=action,
        status="proposed",
        reason="proposed",
        priority_score=0.5,
        eta_to_stopline_s=10.0,
        schedule_delay_s=0.0,
        headway_deviation_s=0.0,
        next_edge_id=next_edge_id,
    )


def snapshot(tls_id: str, spillback: bool) -> NetworkStateSnapshot:
    return NetworkStateSnapshot(tls_id=tls_id, timestamp_s=0.0, spillback_risk=spillback)


class CorridorArbiterTestCase(unittest.TestCase):
    def test_noop_when_corridor_block_absent(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor())  # raw has no "corridor"
        out = arbiter.arbitrate(
            decision(),
            recovery_debt_by_tls={"I1": 999.0},
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)},
        )
        self.assertTrue(out.allow)
        self.assertIsNone(out.reason_code)
        self.assertIsNone(out.note)

    def test_present_but_empty_corridor_block_is_a_noop(self) -> None:
        arbiter = CorridorArbiter(CITS, TSPConfig(root=ROOT, raw={"corridor": {}}))
        out = arbiter.arbitrate(
            decision(),
            recovery_debt_by_tls={"I1": 999.0},
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)},
        )
        self.assertTrue(out.allow)
        self.assertIsNone(out.reason_code)
        self.assertIsNone(out.note)

    def test_zero_cap_is_disabled_not_defer_all(self) -> None:
        # 0 (or negative) means "disabled": the first intervention is still allowed.
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(max_corridor_recovery_debt_s=0))
        self.assertTrue(arbiter.arbitrate(decision(), recovery_debt_by_tls={}).allow)

    def test_bool_cap_is_ignored(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(max_corridor_recovery_debt_s=True))
        self.assertTrue(arbiter.arbitrate(decision(), recovery_debt_by_tls={"I1": 999.0}).allow)

    def test_shipped_default_config_is_a_noop(self) -> None:
        # The real tsp_safety_config.json ships a corridor block with everything
        # disabled (null cap, false flags) -> behaviour-neutral.
        tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        arbiter = CorridorArbiter(CITS, tsp)
        out = arbiter.arbitrate(
            decision(),
            recovery_debt_by_tls={"I1": 999.0},
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)},
        )
        self.assertTrue(out.allow)
        self.assertIsNone(out.reason_code)

    def test_defers_when_corridor_recovery_debt_exhausted(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(max_corridor_recovery_debt_s=10.0))
        out = arbiter.arbitrate(decision(), recovery_debt_by_tls={"I1": 6.0, "I2": 5.0})
        self.assertFalse(out.allow)
        self.assertEqual(out.reason_code, ReasonCode.DEFERRED_CORRIDOR_RECOVERY_DEBT_EXHAUSTED.value)

    def test_allows_when_debt_under_cap(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(max_corridor_recovery_debt_s=10.0))
        out = arbiter.arbitrate(decision(), recovery_debt_by_tls={"I1": 3.0, "I2": 1.0})
        self.assertTrue(out.allow)

    def test_defers_on_downstream_spillback_when_respected(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(respect_downstream_spillback=True))
        out = arbiter.arbitrate(
            decision(),
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)},
        )
        self.assertFalse(out.allow)
        self.assertEqual(out.reason_code, ReasonCode.DEFERRED_DOWNSTREAM_SPILLBACK_RISK.value)

    def test_flags_green_wave_without_deferring(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(flag_green_wave=True))
        out = arbiter.arbitrate(
            decision(),
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)},
        )
        self.assertTrue(out.allow)  # flag, do not defer
        self.assertIsNotNone(out.note)
        self.assertIn("corridor_green_wave_watch", out.note)
        self.assertIn(DOWNSTREAM_TLS, out.note)

    def test_no_action_when_downstream_not_at_spillback(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(respect_downstream_spillback=True, flag_green_wave=True))
        out = arbiter.arbitrate(
            decision(),
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, False)},
        )
        self.assertTrue(out.allow)
        self.assertIsNone(out.note)

    def test_unknown_downstream_edge_is_ignored(self) -> None:
        arbiter = CorridorArbiter(CITS, tsp_with_corridor(respect_downstream_spillback=True))
        out = arbiter.arbitrate(
            decision(next_edge_id="NOT_A_CORRIDOR_EDGE"),
            network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)},
        )
        self.assertTrue(out.allow)

    def test_deferred_outcomes_are_downgrade_only(self) -> None:
        # Invariant: every defer is allow=False with a registered ReasonCode the
        # controller maps to NOT_ACTUABLE (never APPROVED).
        codes = {c.value for c in ReasonCode}
        debt = CorridorArbiter(CITS, tsp_with_corridor(max_corridor_recovery_debt_s=1.0)).arbitrate(
            decision(), recovery_debt_by_tls={"I1": 5.0}
        )
        spill = CorridorArbiter(CITS, tsp_with_corridor(respect_downstream_spillback=True)).arbitrate(
            decision(), network_states={DOWNSTREAM_TLS: snapshot(DOWNSTREAM_TLS, True)}
        )
        for out in (debt, spill):
            self.assertFalse(out.allow)
            self.assertIn(out.reason_code, codes)


if __name__ == "__main__":
    unittest.main()
