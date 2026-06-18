#!/usr/bin/env python3
"""Demonstração real de TSP em Ingolstadt: par baseline-vs-TSP, KPIs, IC95.

Para cada seed corre dois braços na MESMA rede/procura/seed, diferindo só na
atuação TSP:
  - baseline: SUMO simples (sem TSP) — rápido; sem comandos, idêntico ao braço
    no-actuation com a mesma seed.
  - tsp:      o TSPControlController real via TraCI, com atuação (green_extension/
    early_green sob a Safety Layer).

Extrai KPIs do tripinfo (autocarros city-wide + lente da Linha 11 + tráfego
geral), empareLha por seed e reporta a melhoria média do time-loss dos autocarros
com IC95 t-Student (significativo só quando o IC exclui zero). Não fabrica nada:
rede, procura calibrada por detetores e GTFS reais de Ingolstadt (TUM-VT).

Exemplos:
  python scripts/run_ingolstadt_kpi_demo.py --steps 1800 --seeds 57
  python scripts/run_ingolstadt_kpi_demo.py --begin 07:00:00 --steps 3600 --seeds 57 58 59
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for entry in (str(SRC), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from run_ingolstadt_demo import WORK, materialize  # noqa: E402

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_sumo.stats import mean_ci95  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402

_COMMENT = re.compile(rb"<!--.*?-->", re.S)


def _strip(path: Path) -> bytes:
    """Lê XML do SUMO removendo comentários (o dir do repo tem '---', ilegal em comentário XML)."""
    return _COMMENT.sub(b"", path.read_bytes())


def vehicle_line_map(trips_path: Path) -> dict[str, str]:
    root = ET.fromstring(_strip(trips_path))
    return {t.get("id"): (t.get("line") or "") for t in root.iter("trip") if t.get("id")}


def read_tripinfo(path: Path, line_map: dict[str, str]) -> list[dict]:
    root = ET.fromstring(_strip(path))
    rows: list[dict] = []
    for node in root.iter("tripinfo"):
        vid = node.get("id", "")
        vtype = (node.get("vType", "") or "").lower()
        is_bus = vtype.startswith("bus") or vid.lower().startswith("bus")
        rows.append(
            {
                "id": vid,
                "is_bus": is_bus,
                "line": line_map.get(vid, ""),
                "time_loss": float(node.get("timeLoss", 0.0)),
                "duration": float(node.get("duration", 0.0)),
            }
        )
    return rows


def kpis(rows: list[dict], line: str) -> dict:
    buses = [r for r in rows if r["is_bus"]]
    line_buses = [r for r in buses if r["line"] == line]
    general = [r for r in rows if not r["is_bus"]]

    def avg(items: list[dict], key: str) -> float | None:
        return round(mean([r[key] for r in items]), 2) if items else None

    return {
        "n_vehicles": len(rows),
        "n_buses": len(buses),
        "bus_mean_time_loss_s": avg(buses, "time_loss"),
        "bus_mean_duration_s": avg(buses, "duration"),
        f"n_line_{line}_buses": len(line_buses),
        f"line_{line}_bus_mean_time_loss_s": avg(line_buses, "time_loss"),
        "n_general": len(general),
        "general_mean_time_loss_s": avg(general, "time_loss"),
    }


def _begin_seconds(begin: str) -> int:
    h, m, s = (int(p) for p in begin.split(":"))
    return h * 3600 + m * 60 + s


def _seconds_to_hhmmss(total: int) -> str:
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def run_baseline(seed: int, end_str: str, out_rel: str) -> None:
    """SUMO simples (sem TSP), mesma seed. cwd=WORK + paths relativos (gotcha '---')."""
    cmd = [
        "sumo", "-c", "demo.sumocfg",
        "--seed", str(seed),
        "--end", end_str,
        "--tripinfo-output", out_rel,
        "--no-step-log", "--no-warnings", "--duration-log.statistics",
    ]
    subprocess.run(cmd, cwd=WORK, check=True, capture_output=True)


def run_tsp(args: argparse.Namespace, sumocfg: Path, net: Path, seed: int, out_path: Path) -> dict:
    """Braço TSP: o controller real via TraCI, com atuação. Devolve o summary."""
    cits = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    cits["sumo"]["sumocfg"] = str(sumocfg)
    cits["sumo"]["network"] = str(net)
    schedule_plan = cits.get("schedule_plan", {})
    if isinstance(schedule_plan, dict) and str(schedule_plan.get("mode", "")).lower() == "gtfs":
        schedule_plan["gtfs_trips"] = str(WORK / "PT" / f"{args.day}_gtfs_trips.rou.xml")
        schedule_plan["pt_stops"] = str(WORK / "PT" / "pt_stops.add.xml")
    out = WORK / "out"
    cits["logging"] = {
        "message_log": str(out / f"_kpi_cits_{seed}.jsonl"),
        "summary_report": str(out / f"_kpi_cits_sum_{seed}.json"),
        "mapem_snapshot": str(out / f"_kpi_map_{seed}.json"),
        "spatem_snapshot": str(out / f"_kpi_spat_{seed}.json"),
    }
    cits_path = WORK / f"_kpi_cits_{seed}.json"
    cits_path.write_text(json.dumps(cits), encoding="utf-8")

    tsp = json.loads((ROOT / args.tsp_config).read_text(encoding="utf-8"))
    tsp["scenario_id"] = "ingolstadt_kpi_demo"
    tsp.setdefault("logging", {}).update(
        {
            "decision_log": str(out / f"_kpi_dec_{seed}.jsonl"),
            "actuation_log": str(out / f"_kpi_act_{seed}.jsonl"),
            "summary_report": str(out / f"_kpi_tsp_sum_{seed}.json"),
        }
    )
    tsp_path = WORK / f"_kpi_tsp_{seed}.json"
    tsp_path.write_text(json.dumps(tsp), encoding="utf-8")

    cits_config = load_cits_config(cits_path, root=ROOT)
    tsp_config = load_tsp_config(tsp_path, root=ROOT)
    controller = TSPControlController(cits_config, tsp_config)
    return controller.run_with_sumo(
        steps=args.steps,
        sumo_binary="sumo",
        apply_actuation=True,
        extra_args=["--seed", str(seed), "--tripinfo-output", str(out_path)],
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", default="2023-07-04")
    p.add_argument("--begin", default="07:00:00")
    p.add_argument("--steps", type=int, default=1800, help="Comprimento da janela em passos (1s).")
    p.add_argument("--seeds", type=int, nargs="+", default=[57])
    p.add_argument("--line", default="11", help="Linha para a lente de atribuição causal.")
    p.add_argument("--config", default="configs/cits_ingolstadt_config.json", type=Path)
    p.add_argument("--tsp-config", default="configs/tsp_safety_config.json", type=Path)
    p.add_argument("--out", default="reports/ingolstadt/kpi_demo.json", type=Path)
    args = p.parse_args()

    sumocfg, net = materialize(args.day, args.begin, refresh=False)
    line_map = vehicle_line_map(WORK / "PT" / f"{args.day}_gtfs_trips.rou.xml")
    end_str = _seconds_to_hhmmss(_begin_seconds(args.begin) + args.steps)
    out_dir = WORK / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    per_seed: list[dict] = []
    for seed in args.seeds:
        print(f"[seed {seed}] baseline (SUMO simples até {end_str})...", flush=True)
        base_tri = out_dir / f"_kpi_base_{seed}.xml"
        run_baseline(seed, end_str, f"out/{base_tri.name}")
        base_kpi = kpis(read_tripinfo(base_tri, line_map), args.line)

        print(f"[seed {seed}] TSP (controller real, {args.steps} passos)...", flush=True)
        tsp_tri = out_dir / f"_kpi_tsp_{seed}.xml"
        summary = run_tsp(args, sumocfg, net, seed, tsp_tri)
        tsp_kpi = kpis(read_tripinfo(tsp_tri, line_map), args.line)

        per_seed.append(
            {
                "seed": seed,
                "baseline": base_kpi,
                "tsp": tsp_kpi,
                "tsp_decisions": summary.get("total_decisions"),
                "tsp_actuations_applied": summary.get("applied_events"),
                "tsp_blocked_by_safety": summary.get("blocked_by_safety"),
                "green_extension_decisions": summary.get("green_extension_decisions"),
            }
        )
        b, t = base_kpi["bus_mean_time_loss_s"], tsp_kpi["bus_mean_time_loss_s"]
        print(f"[seed {seed}] bus time-loss baseline={b}s tsp={t}s "
              f"melhoria={round((b - t), 2) if b is not None and t is not None else None}s "
              f"| atuações={summary.get('applied_events')}", flush=True)

    # Estatística pareada: melhoria = redução do bus time-loss (lower is better).
    def paired(group_key: str) -> dict:
        deltas = [
            s["baseline"][group_key] - s["tsp"][group_key]
            for s in per_seed
            if s["baseline"].get(group_key) is not None and s["tsp"].get(group_key) is not None
        ]
        ci = mean_ci95(deltas) if deltas else {}
        verdict = "insufficient_seeds"
        low, high = ci.get("ci95_low"), ci.get("ci95_high")
        # Significância só com >=2 seeds emparelhadas (com 1 o IC degenera para a média).
        if len(deltas) >= 2 and low is not None and high is not None:
            if low > 0:
                verdict = "significant_improvement"
            elif high < 0:
                verdict = "significant_regression"
            else:
                verdict = "inconclusive_ci_includes_zero"
        return {"n_seeds": len(deltas), "per_seed_improvement_s": [round(d, 2) for d in deltas],
                **ci, "verdict": verdict}

    report = {
        "scenario": {
            "city": "Ingolstadt (TUM-VT)", "net": net.name, "day": args.day,
            "window": f"{args.begin} +{args.steps}s (até {end_str})", "seeds": args.seeds,
            "line_lens": args.line,
        },
        "method": "Par baseline (SUMO) vs TSP (controller real via TraCI), seeds emparelhadas; "
                  "KPIs do tripinfo; melhoria = redução do bus time-loss; IC95 t-Student.",
        "coverage_note": "TSP atua via green_extension nos TLS de tempo-fixo verificados "
                         "(~86/123 em Ingolstadt 07:00); fail-closed nos restantes (Safety Layer).",
        "no_fabricated_data": "Rede, procura det-calib e GTFS reais de Ingolstadt; nada inventado.",
        "per_seed": per_seed,
        "paired_bus_time_loss_improvement_s": paired("bus_mean_time_loss_s"),
        f"paired_line_{args.line}_bus_time_loss_improvement_s": paired(
            f"line_{args.line}_bus_mean_time_loss_s"
        ),
        "paired_general_time_loss_change_s": paired("general_mean_time_loss_s"),
    }
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    pb = report["paired_bus_time_loss_improvement_s"]
    print("\n==================== RESULTADO ====================")
    print(f"seeds: {args.seeds}  janela: {args.begin} +{args.steps}s")
    print(f"melhoria bus time-loss (city-wide): média {pb.get('mean')}s "
          f"IC95 [{pb.get('ci95_low')}, {pb.get('ci95_high')}] -> {pb.get('verdict')}")
    print(f"relatório: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
