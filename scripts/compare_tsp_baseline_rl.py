#!/usr/bin/env python3
"""Run baseline and RL TSP SUMO/TraCI runs and write a comparison table.

Audit nota: as duas corridas escrevem para os MESMOS caminhos JSONL/JSON
(definidos em tsp_config.logging). Sem snapshot, a corrida RL sobrescreve
silenciosamente os logs da baseline — a comparação ao nível de sumário
ainda funciona, mas os logs raw da baseline ficam perdidos para auditoria
posterior. Este script copia explicitamente os artefactos da baseline para
`--snapshot-root/baseline/` antes da RL correr, e da RL para
`--snapshot-root/rl/` no fim. Mesmo padrão usado em
`scripts/evaluate_decision_outcomes.py`.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_opt.ab_compare import write_tsp_ab_comparison  # noqa: E402
from pps57_opt.config import load_policy_optimization_config  # noqa: E402
from pps57_opt.event_dataset import write_event_training_dataset  # noqa: E402
from pps57_opt.rl_trainer import TabularQLearningController  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402


SNAPSHOT_PATHS = (
    "outputs/tsp_decisions.jsonl",
    "outputs/tsp_actuation.jsonl",
    "outputs/cits_messages.jsonl",
    "reports/tsp_emulation_summary.json",
    "reports/cits_emulation_summary.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare TSP baseline runtime against exported RL runtime policy.")
    parser.add_argument("--config", default="configs/cits_v2x_config.json", help="Base C-ITS configuration.")
    parser.add_argument("--tsp-config", default="configs/tsp_safety_config.json", help="TSP/Safety Layer configuration.")
    parser.add_argument("--policy-config", default="configs/policy_training_config.json", help="RL training configuration.")
    parser.add_argument("--policy-report", default="reports/tabular_q_policy_report.json", help="Exported RL policy report.")
    parser.add_argument("--steps", type=int, default=7200, help="SUMO/TraCI steps for both modes.")
    parser.add_argument("--sumo-binary", default="sumo", help="SUMO binary for TraCI.")
    parser.add_argument("--no-actuation", action="store_true", help="Calculate decisions without applying TraCI commands.")
    parser.add_argument("--train-rl", action="store_true", help="Train/export the RL policy before comparing.")
    parser.add_argument("--json-out", default="reports/tsp_baseline_vs_rl_comparison.json", help="JSON comparison output.")
    parser.add_argument("--md-out", default="reports/tsp_baseline_vs_rl_comparison.md", help="Markdown table output.")
    parser.add_argument(
        "--snapshot-root",
        default="outputs/runs",
        help="Diretório raiz para snapshots imutáveis dos artefactos baseline/RL (auditoria).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cits_config = load_cits_config(ROOT / args.config, root=ROOT)
    tsp_config = load_tsp_config(ROOT / args.tsp_config, root=ROOT)
    optimization_config = load_policy_optimization_config(ROOT / args.policy_config, root=ROOT)
    policy_report = tsp_config.path_from_root(args.policy_report)
    snapshot_root = tsp_config.path_from_root(args.snapshot_root)

    baseline_summary = TSPControlController(
        cits_config,
        tsp_config,
        policy_mode="baseline",
    ).run_with_sumo(steps=args.steps, sumo_binary=args.sumo_binary, apply_actuation=not args.no_actuation)
    baseline_snapshot = _snapshot_artifacts(ROOT, snapshot_root / "baseline")

    if args.train_rl or not policy_report.exists():
        write_event_training_dataset(
            cits_log=cits_config.path_from_root(cits_config.logging.get("message_log", "outputs/cits_messages.jsonl")),
            decision_log=tsp_config.path_from_root(tsp_config.logging.get("decision_log", "outputs/tsp_decisions.jsonl")),
            actuation_log=tsp_config.path_from_root(tsp_config.logging.get("actuation_log", "outputs/tsp_actuation.jsonl")),
            output_path=optimization_config.path_from_root(
                optimization_config.logging.get("event_training_dataset", "outputs/event_training_dataset.jsonl")
            ),
        )
        TabularQLearningController(cits_config, tsp_config, optimization_config).run()

    rl_summary = TSPControlController(
        cits_config,
        tsp_config,
        policy_mode="rl",
        policy_report_path=str(policy_report),
    ).run_with_sumo(steps=args.steps, sumo_binary=args.sumo_binary, apply_actuation=not args.no_actuation)
    rl_snapshot = _snapshot_artifacts(ROOT, snapshot_root / "rl")

    payload = write_tsp_ab_comparison(
        baseline_summary,
        rl_summary,
        json_path=tsp_config.path_from_root(args.json_out),
        markdown_path=tsp_config.path_from_root(args.md_out),
    )

    print("TSP baseline vs RL comparison:")
    print(f"- rows: {len(payload['rows'])}")
    print(f"- json: {tsp_config.path_from_root(args.json_out)}")
    print(f"- markdown: {tsp_config.path_from_root(args.md_out)}")
    print(f"- baseline snapshot: {baseline_snapshot} ({len(list(baseline_snapshot.rglob('*')))} files)")
    print(f"- rl snapshot:       {rl_snapshot} ({len(list(rl_snapshot.rglob('*')))} files)")
    return 0


def _snapshot_artifacts(source_root: Path, dest_root: Path) -> Path:
    """Copia logs/sumários TSP+CITS para um diretório imutável de auditoria.

    Sem isto, a corrida seguinte (RL) sobrescreve os JSONL nos mesmos caminhos
    configurados e os logs raw da baseline ficam irrecuperáveis.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    for rel in SNAPSHOT_PATHS:
        src = source_root / rel
        if not src.exists():
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dest_root


if __name__ == "__main__":
    raise SystemExit(main())
