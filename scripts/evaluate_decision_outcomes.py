#!/usr/bin/env python3
"""Evaluate baseline vs RL decision outcomes conservatively."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_opt.config import load_policy_optimization_config  # noqa: E402
from pps57_opt.event_dataset import write_event_training_dataset  # noqa: E402
from pps57_opt.outcome_evaluator import write_decision_outcome_evaluation  # noqa: E402
from pps57_opt.rl_trainer import TabularQLearningController  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate whether RL TSP decisions are safer, worse, or inconclusive."
    )
    parser.add_argument(
        "--config", default="configs/cits_v2x_config.json", help="Base C-ITS configuration."
    )
    parser.add_argument(
        "--tsp-config",
        default="configs/tsp_safety_config.json",
        help="TSP/Safety Layer configuration.",
    )
    parser.add_argument(
        "--policy-config",
        default="configs/policy_training_config.json",
        help="RL training configuration.",
    )
    parser.add_argument(
        "--policy-report",
        default="reports/tabular_q_policy_report.json",
        help="Exported RL policy report.",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=14400,
        help="SUMO/TraCI steps when generating fresh paired runs (14400 × 0.5s/step "
        "= full 7200s window; B3: the old default 7200 covered only half the window).",
    )
    parser.add_argument("--sumo-binary", default="sumo", help="SUMO binary for TraCI.")
    parser.add_argument(
        "--no-actuation",
        action="store_true",
        help="Calculate decisions without applying TraCI commands.",
    )
    parser.add_argument(
        "--train-rl", action="store_true", help="Train/export the RL policy before evaluating."
    )
    parser.add_argument(
        "--baseline-root",
        type=Path,
        default=None,
        help="Existing baseline run root with reports/ and outputs/.",
    )
    parser.add_argument(
        "--rl-root",
        type=Path,
        default=None,
        help="Existing RL run root with reports/ and outputs/.",
    )
    parser.add_argument(
        "--baseline-kpis", type=Path, default=None, help="Optional baseline SUMO KPI JSON."
    )
    parser.add_argument("--rl-kpis", type=Path, default=None, help="Optional RL SUMO KPI JSON.")
    parser.add_argument("--json-out", default="reports/decision_outcome_evaluation.json")
    parser.add_argument("--md-out", default="reports/decision_outcome_evaluation.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cits_config = load_cits_config(ROOT / args.config, root=ROOT)
    tsp_config = load_tsp_config(ROOT / args.tsp_config, root=ROOT)
    optimization_config = load_policy_optimization_config(ROOT / args.policy_config, root=ROOT)
    policy_report = tsp_config.path_from_root(args.policy_report)

    # B22: the two roots must be supplied together. Passing only one used to fall
    # through to a fresh SUMO run that overwrites outputs/ — surprising and silent.
    if bool(args.baseline_root) != bool(args.rl_root):
        raise SystemExit(
            "B22: --baseline-root e --rl-root têm de ser fornecidos JUNTOS; passar só um "
            "faria fallback silencioso a um run SUMO novo (sobrescrevendo outputs/)."
        )
    # B23: when explicit KPIs are not given, derive them from a tripinfo.xml under the
    # provided root so network_impact_verdict isn't stuck at inconclusive_without_kpis.
    baseline_kpis = _read_optional_json(args.baseline_kpis) or _kpis_from_root(args.baseline_root)
    rl_kpis = _read_optional_json(args.rl_kpis) or _kpis_from_root(args.rl_root)
    if args.baseline_root and args.rl_root:
        payload = _evaluate_existing_roots(
            baseline_root=args.baseline_root,
            rl_root=args.rl_root,
            json_out=tsp_config.path_from_root(args.json_out),
            md_out=tsp_config.path_from_root(args.md_out),
            baseline_kpis=baseline_kpis,
            rl_kpis=rl_kpis,
        )
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            baseline_root = tmp_root / "baseline"
            rl_root = tmp_root / "rl"
            baseline_summary = TSPControlController(
                cits_config,
                tsp_config,
                policy_mode="baseline",
            ).run_with_sumo(
                steps=args.steps,
                sumo_binary=args.sumo_binary,
                apply_actuation=not args.no_actuation,
            )
            _copy_tsp_artifacts(ROOT, baseline_root)
            if args.train_rl or not policy_report.exists():
                write_event_training_dataset(
                    cits_log=cits_config.path_from_root(
                        cits_config.logging.get("message_log", "outputs/cits_messages.jsonl")
                    ),
                    decision_log=tsp_config.path_from_root(
                        tsp_config.logging.get("decision_log", "outputs/tsp_decisions.jsonl")
                    ),
                    actuation_log=tsp_config.path_from_root(
                        tsp_config.logging.get("actuation_log", "outputs/tsp_actuation.jsonl")
                    ),
                    output_path=optimization_config.path_from_root(
                        optimization_config.logging.get(
                            "event_training_dataset", "outputs/event_training_dataset.jsonl"
                        )
                    ),
                )
                TabularQLearningController(cits_config, tsp_config, optimization_config).run()
            rl_summary = TSPControlController(
                cits_config,
                tsp_config,
                policy_mode="rl",
                policy_report_path=str(policy_report),
            ).run_with_sumo(
                steps=args.steps,
                sumo_binary=args.sumo_binary,
                apply_actuation=not args.no_actuation,
            )
            _copy_tsp_artifacts(ROOT, rl_root)
            payload = _write(
                baseline_root=baseline_root,
                rl_root=rl_root,
                baseline_summary=baseline_summary,
                rl_summary=rl_summary,
                json_out=tsp_config.path_from_root(args.json_out),
                md_out=tsp_config.path_from_root(args.md_out),
                baseline_kpis=baseline_kpis,
                rl_kpis=rl_kpis,
            )

    print("Decision outcome evaluation:")
    print(f"- decisions: {payload['decision_count']}")
    print(f"- matched: {payload['matched_decision_count']}")
    print(f"- network_impact_verdict: {payload['network_impact_verdict']}")
    print(f"- verdict_counts: {payload['verdict_counts']}")
    print(f"- json: {tsp_config.path_from_root(args.json_out)}")
    print(f"- markdown: {tsp_config.path_from_root(args.md_out)}")
    return 0


def _evaluate_existing_roots(
    *,
    baseline_root: Path,
    rl_root: Path,
    json_out: Path,
    md_out: Path,
    baseline_kpis: dict | None,
    rl_kpis: dict | None,
) -> dict:
    baseline_summary = json.loads(
        (baseline_root / "reports/tsp_emulation_summary.json").read_text(encoding="utf-8")
    )
    rl_summary = json.loads(
        (rl_root / "reports/tsp_emulation_summary.json").read_text(encoding="utf-8")
    )
    return _write(
        baseline_root=baseline_root,
        rl_root=rl_root,
        baseline_summary=baseline_summary,
        rl_summary=rl_summary,
        json_out=json_out,
        md_out=md_out,
        baseline_kpis=baseline_kpis,
        rl_kpis=rl_kpis,
    )


def _write(
    *,
    baseline_root: Path,
    rl_root: Path,
    baseline_summary: dict,
    rl_summary: dict,
    json_out: Path,
    md_out: Path,
    baseline_kpis: dict | None,
    rl_kpis: dict | None,
) -> dict:
    return write_decision_outcome_evaluation(
        baseline_summary=baseline_summary,
        rl_summary=rl_summary,
        baseline_decision_log=baseline_root / "outputs/tsp_decisions.jsonl",
        baseline_actuation_log=baseline_root / "outputs/tsp_actuation.jsonl",
        rl_decision_log=rl_root / "outputs/tsp_decisions.jsonl",
        rl_actuation_log=rl_root / "outputs/tsp_actuation.jsonl",
        baseline_kpis=baseline_kpis,
        rl_kpis=rl_kpis,
        json_path=json_out,
        markdown_path=md_out,
    )


def _read_optional_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _kpis_from_root(root: Path | None) -> dict | None:
    """B23: derive KPIs from a tripinfo.xml under `root` when none were passed.

    Lets network_impact_verdict be computed from a real run directory instead of
    staying at inconclusive_without_kpis. Returns None when no usable tripinfo exists.
    """
    if root is None:
        return None
    for rel in ("tripinfo.xml", "outputs/tripinfo.xml"):
        candidate = Path(root) / rel
        if candidate.exists():
            kpis = parse_tripinfo(candidate)
            if not kpis.get("tripinfo_parse_error"):
                return kpis
    return None


def _copy_tsp_artifacts(source_root: Path, dest_root: Path) -> None:
    for rel in [
        "outputs/tsp_decisions.jsonl",
        "outputs/tsp_actuation.jsonl",
        "reports/tsp_emulation_summary.json",
    ]:
        src = source_root / rel
        if not src.exists():
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


if __name__ == "__main__":
    raise SystemExit(main())
