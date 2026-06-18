#!/usr/bin/env python3
"""Demonstração real de TSP em Ingolstadt: par baseline-vs-TSP, KPIs, IC95.

Para cada seed corre dois braços na MESMA rede/procura/seed/passos, diferindo só
no toggle de atuação (o MESMO TSPControlController via TraCI nos dois):
  - baseline: o controller em dry-run (apply_actuation=False) — decide mas não
    atua; é o braço no-actuation, garantindo equivalência ao TSP por construção.
  - tsp:      o controller com atuação (green_extension/early_green sob a Safety
    Layer).

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
from copy import deepcopy
import json
import re
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for entry in (str(SRC), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from run_ingolstadt_demo import WORK, materialize  # noqa: E402

# M4: defusedxml prefere-se para parsing robusto de XML em cenários de integração.
try:
    from defusedxml import ElementTree as ET  # noqa: S314
except ImportError:  # pragma: no cover - fallback for minimal envs without defusedxml.
    from xml.etree import ElementTree as ET  # noqa: S314

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_sumo.stats import mean_ci95  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402

_COMMENT = re.compile(rb"<!--.*?-->", re.S)


def _strip(path: Path) -> bytes:
    """Lê XML do SUMO removendo comentários (o dir do repo tem '---', ilegal em comentário XML)."""
    return _COMMENT.sub(b"", path.read_bytes())


def vehicle_line_map(trips_path: Path) -> dict[str, str]:
    root = ET.fromstring(_strip(trips_path))  # noqa: S314
    return {t.get("id"): (t.get("line") or "") for t in root.iter("trip") if t.get("id")}


def read_tripinfo(path: Path, line_map: dict[str, str]) -> list[dict]:
    root = ET.fromstring(_strip(path))  # noqa: S314
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
                "waiting_time": float(node.get("waitingTime", 0.0)),
                "stop_count": float(node.get("waitingCount", 0.0)),
                "depart_delay": float(node.get("departDelay", 0.0)),
                "route_length": float(node.get("routeLength", 0.0)),
            }
        )
    return rows


def _summarize(items: list[dict]) -> dict:
    """KPIs por grupo de veículos (todos do tripinfo; nada fabricado)."""

    def _avg(key: str) -> float | None:
        return round(mean([r[key] for r in items]), 2) if items else None

    def _p95(key: str) -> float | None:
        vals = sorted(r[key] for r in items)
        if not vals:
            return None
        index = max(0, min(len(vals) - 1, int(0.95 * len(vals))))
        return round(vals[index], 2)

    speeds = [r["route_length"] / r["duration"] for r in items if r["duration"] > 0]
    return {
        "n": len(items),
        "mean_time_loss_s": _avg("time_loss"),
        "p95_time_loss_s": _p95("time_loss"),
        "mean_waiting_time_s": _avg("waiting_time"),
        "mean_stop_count": _avg("stop_count"),
        "mean_speed_mps": round(mean(speeds), 2) if speeds else None,
        "mean_duration_s": _avg("duration"),
        "mean_depart_delay_s": _avg("depart_delay"),
    }


def kpis(rows: list[dict], line: str) -> dict:
    buses = [r for r in rows if r["is_bus"]]
    line_buses = [r for r in buses if r["line"] == line]
    general = [r for r in rows if not r["is_bus"]]
    bus, lin, gen = _summarize(buses), _summarize(line_buses), _summarize(general)
    # Achatado para a comparação pareada por chave (paired() acede a chaves planas).
    out = {"n_vehicles": len(rows), "throughput_completed": len(rows)}
    for prefix, summary in (("bus", bus), (f"line_{line}_bus", lin), ("general", gen)):
        for key, value in summary.items():
            out[f"{prefix}_{key}" if key != "n" else f"n_{prefix}"] = value
    return out


def _begin_seconds(begin: str) -> int:
    h, m, s = (int(p) for p in begin.split(":"))
    return h * 3600 + m * 60 + s


def _seconds_to_hhmmss(total: int) -> str:
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def run_arm(
    args: argparse.Namespace,
    sumocfg: Path,
    net: Path,
    seed: int,
    out_path: Path,
    cits_template: dict,
    tsp_template: dict,
    *,
    apply_actuation: bool,
    arm: str,
) -> dict:
    """Corre um braço (o MESMO controller via TraCI); devolve (summary, stats_path).

    Com ``apply_actuation=False`` é o baseline (dry-run: decide mas não atua);
    com ``True`` é o braço TSP. Os dois braços partilham rede/procura/seed/passos,
    diferindo só no toggle — daí a equivalência baseline≡no-actuation por
    construção. ``arm`` prefixa os ficheiros de log para os braços não colidirem.
    """
    cits = deepcopy(cits_template)
    cits["sumo"]["sumocfg"] = str(sumocfg)
    cits["sumo"]["network"] = str(net)
    schedule_plan = cits.get("schedule_plan", {})
    if isinstance(schedule_plan, dict) and str(schedule_plan.get("mode", "")).lower() == "gtfs":
        schedule_plan["gtfs_trips"] = str(WORK / "PT" / f"{args.day}_gtfs_trips.rou.xml")
        schedule_plan["pt_stops"] = str(WORK / "PT" / "pt_stops.add.xml")
    out = WORK / "out"
    cits["logging"] = {
        "message_log": str(out / f"_kpi_{arm}_cits_{seed}.jsonl"),
        "summary_report": str(out / f"_kpi_{arm}_cits_sum_{seed}.json"),
        "mapem_snapshot": str(out / f"_kpi_{arm}_map_{seed}.json"),
        "spatem_snapshot": str(out / f"_kpi_{arm}_spat_{seed}.json"),
    }
    cits_path = WORK / f"_kpi_{arm}_cits_{seed}.json"
    cits_path.write_text(json.dumps(cits), encoding="utf-8")

    tsp = deepcopy(tsp_template)
    tsp["scenario_id"] = f"ingolstadt_kpi_demo_{arm}"
    tsp.setdefault("logging", {}).update(
        {
            "decision_log": str(out / f"_kpi_{arm}_dec_{seed}.jsonl"),
            "actuation_log": str(out / f"_kpi_{arm}_act_{seed}.jsonl"),
            "summary_report": str(out / f"_kpi_{arm}_tsp_sum_{seed}.json"),
        }
    )
    tsp_path = WORK / f"_kpi_{arm}_tsp_{seed}.json"
    tsp_path.write_text(json.dumps(tsp), encoding="utf-8")

    cits_config = load_cits_config(cits_path, root=ROOT)
    tsp_config = load_tsp_config(tsp_path, root=ROOT)
    controller = TSPControlController(cits_config, tsp_config)
    stats_path = out / f"_kpi_{arm}_stats_{seed}.xml"
    summary = controller.run_with_sumo(
        steps=args.steps,
        sumo_binary="sumo",
        apply_actuation=apply_actuation,
        extra_args=[
            "--seed", str(seed),
            "--tripinfo-output", str(out_path),
            # --statistic-output: teleports/colisões/travagens estruturados (do SUMO,
            # não fabricados). --no-step-log corta o spam de progresso, sem esconder
            # warnings — os defeitos de plano continuam visíveis.
            "--statistic-output", str(stats_path),
            "--no-step-log",
        ],
    )
    return summary, stats_path


def _parse_sumo_statistics(path: Path) -> dict:
    """Lê o --statistic-output do SUMO de um braço (teleports/colisões/travagens).

    Tudo do próprio SUMO (rastreável). Devolve {} se o ficheiro faltar; None por
    campo ausente.
    """
    if not path.exists():
        return {}
    root = ET.parse(str(path)).getroot()

    def _int(tag: str, attr: str) -> int | None:
        el = root.find(tag)
        if el is None or el.get(attr) is None:
            return None
        return int(el.get(attr))

    return {
        "loaded": _int("vehicles", "loaded"),
        "inserted": _int("vehicles", "inserted"),
        "running_end": _int("vehicles", "running"),
        "teleports": _int("teleports", "total"),
        "teleports_jam": _int("teleports", "jam"),
        "teleports_yield": _int("teleports", "yield"),
        "collisions": _int("safety", "collisions"),
        "emergency_stops": _int("safety", "emergencyStops"),
        "emergency_braking": _int("safety", "emergencyBraking"),
    }


def _sim_quality(stats_path: Path, summary: dict) -> dict:
    """Bloco de qualidade de simulação de um braço.

    Dinâmica do SUMO (teleports/colisões — o que pode enviesar KPIs) + cobertura de
    plano do controller: tls_fail_closed = TLS cujos planos calibrados TUM-VT têm
    defeitos (Missing green/conflitos) e ficam fail-closed na Safety Layer. Medido.
    """
    s = _parse_sumo_statistics(stats_path)
    cov = summary.get("signal_program_verification", {})
    loaded, tele = s.get("loaded"), s.get("teleports")
    rate = round(100 * tele / loaded, 3) if loaded and tele is not None else None
    return {
        "vehicles_loaded": loaded,
        "vehicles_inserted": s.get("inserted"),
        "teleports_total": tele,
        "teleports_jam": s.get("teleports_jam"),
        "teleports_yield": s.get("teleports_yield"),
        "teleport_rate_pct": rate,
        "collisions": s.get("collisions"),
        "emergency_stops": s.get("emergency_stops"),
        "emergency_braking": s.get("emergency_braking"),
        "tls_total": cov.get("tls_total"),
        "tls_actuable": cov.get("tls_actuable"),
        "tls_fail_closed": cov.get("tls_fail_closed"),
    }


def _paired_improvement(
    per_seed: list[dict],
    group_key: str,
    *,
    lower_is_better: bool = True,
) -> dict:
    """Calcula melhoria por seed emparelhada e IC95 para uma métrica."""
    deltas = [
        (seed_data["baseline"][group_key] - seed_data["tsp"][group_key])
        if lower_is_better
        else (seed_data["tsp"][group_key] - seed_data["baseline"][group_key])
        for seed_data in per_seed
        if seed_data["baseline"].get(group_key) is not None
        and seed_data["tsp"].get(group_key) is not None
    ]
    ci = mean_ci95(deltas) if deltas else {}
    verdict = "insufficient_seeds"
    low, high = ci.get("ci95_low"), ci.get("ci95_high")
    if len(deltas) >= 2 and low is not None and high is not None:
        if low > 0:
            verdict = "significant_improvement"
        elif high < 0:
            verdict = "significant_regression"
        else:
            verdict = "inconclusive_ci_includes_zero"

    return {
        "n_seeds": len(deltas),
        "per_seed_improvement_s": [round(d, 2) for d in deltas],
        **ci,
        "verdict": verdict,
    }


def _paired_reports(per_seed: list[dict], line: str) -> dict[str, dict]:
    metrics = [
        ("paired_bus_time_loss_improvement_s", "bus_mean_time_loss_s", True),
        ("paired_bus_p95_time_loss_improvement_s", "bus_p95_time_loss_s", True),
        ("paired_bus_waiting_time_improvement_s", "bus_mean_waiting_time_s", True),
        ("paired_bus_stop_count_improvement", "bus_mean_stop_count", True),
        ("paired_bus_speed_gain_mps", "bus_mean_speed_mps", False),
        ("paired_general_time_loss_change_s", "general_mean_time_loss_s", True),
        ("paired_general_waiting_time_change_s", "general_mean_waiting_time_s", True),
        (
            f"paired_line_{line}_bus_time_loss_improvement_s",
            f"line_{line}_bus_mean_time_loss_s",
            True,
        ),
    ]
    return {
        report_name: _paired_improvement(
            per_seed, metric_name, lower_is_better=lower_is_better
        )
        for report_name, metric_name, lower_is_better in metrics
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
    cits_template = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    tsp_template = json.loads((ROOT / args.tsp_config).read_text(encoding="utf-8"))
    line_map = vehicle_line_map(WORK / "PT" / f"{args.day}_gtfs_trips.rou.xml")
    end_str = _seconds_to_hhmmss(_begin_seconds(args.begin) + args.steps)
    out_dir = WORK / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    per_seed: list[dict] = []
    for seed in args.seeds:
        print(f"[seed {seed}] baseline (controller dry-run, {args.steps} passos)...", flush=True)
        base_tri = out_dir / f"_kpi_base_{seed}.xml"
        base_summary, base_stats = run_arm(
            args, sumocfg, net, seed, base_tri, cits_template, tsp_template,
            apply_actuation=False, arm="base",
        )
        base_kpi = kpis(read_tripinfo(base_tri, line_map), args.line)

        print(f"[seed {seed}] TSP (controller real, {args.steps} passos)...", flush=True)
        tsp_tri = out_dir / f"_kpi_tsp_{seed}.xml"
        summary, tsp_stats = run_arm(
            args, sumocfg, net, seed, tsp_tri, cits_template, tsp_template,
            apply_actuation=True, arm="tsp",
        )
        tsp_kpi = kpis(read_tripinfo(tsp_tri, line_map), args.line)

        base_quality = _sim_quality(base_stats, base_summary)
        tsp_quality = _sim_quality(tsp_stats, summary)
        per_seed.append(
            {
                "seed": seed,
                "baseline": base_kpi,
                "tsp": tsp_kpi,
                # Prova de que o controlo não atuou: aplicações reais no baseline = 0.
                "baseline_actuations_applied": base_summary.get("applied_events"),
                # Qualidade de simulação por braço (teleports/colisões + cobertura TLS).
                "baseline_quality": base_quality,
                "tsp_quality": tsp_quality,
                "tsp_decisions": summary.get("total_decisions"),
                "tsp_approved": summary.get("approved_decisions"),
                "tsp_actuations_applied": summary.get("applied_events"),
                "tsp_real_traci_applied": summary.get("real_traci_applied_events"),
                "tsp_blocked_by_safety": summary.get("blocked_by_safety"),
                "green_extension_decisions": summary.get("green_extension_decisions"),
                "early_green_decisions": summary.get("early_green_decisions"),
            }
        )
        b, t = base_kpi["bus_mean_time_loss_s"], tsp_kpi["bus_mean_time_loss_s"]
        print(f"[seed {seed}] bus time-loss baseline={b}s tsp={t}s "
              f"melhoria={round((b - t), 2) if b is not None and t is not None else None}s "
              f"| atuações={summary.get('applied_events')}", flush=True)
        print(f"[seed {seed}] qualidade: teleports base={base_quality['teleport_rate_pct']}% "
              f"({base_quality['teleports_total']}) tsp={tsp_quality['teleport_rate_pct']}% "
              f"({tsp_quality['teleports_total']}) | TLS actuáveis "
              f"{tsp_quality['tls_actuable']}/{tsp_quality['tls_total']} "
              f"(fail-closed {tsp_quality['tls_fail_closed']})", flush=True)

    # Cobertura TLS é estática (igual nos dois braços); usa a do baseline da 1ª seed.
    cov = per_seed[0].get("baseline_quality", {}) if per_seed else {}
    report = {
        "scenario": {
            "city": "Ingolstadt (TUM-VT)", "net": net.name, "day": args.day,
            "window": f"{args.begin} +{args.steps}s (até {end_str})", "seeds": args.seeds,
            "line": args.line,
        },
        "method": "Par baseline (controller em dry-run, apply_actuation=False) vs TSP "
                  "(mesmo controller com atuação via TraCI), seeds/rede/procura/passos "
                  "emparelhados — só difere o toggle de atuação; KPIs do tripinfo; "
                  "melhoria = redução do bus time-loss; IC95 t-Student.",
        "coverage_note": (
            f"TSP atua via green_extension em {cov.get('tls_actuable')} dos "
            f"{cov.get('tls_total')} TLS sinalizados; fail-closed nos "
            f"{cov.get('tls_fail_closed')} restantes (Safety Layer)."
        ),
        "simulation_quality_note": (
            "per_seed[].baseline_quality/tsp_quality: teleports/colisões/travagens vêm do "
            "--statistic-output do SUMO; tls_fail_closed é a cobertura do controller. Os "
            "defeitos dos planos calibrados TUM-VT (Missing green/conflitos) manifestam-se "
            "como TLS fail-closed, simétricos entre braços — limitação conhecida da fonte, "
            "não fabricada."
        ),
        "no_fabricated_data": "Rede, procura det-calib e GTFS reais de Ingolstadt; nada inventado.",
        "per_seed": per_seed,
        **_paired_reports(per_seed, args.line),
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
