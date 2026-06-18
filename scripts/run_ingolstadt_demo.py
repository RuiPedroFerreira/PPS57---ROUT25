#!/usr/bin/env python3
"""Corre o motor TSP city-wide na rede real e calibrada de Ingolstadt.

Aponta o TSPControlController ao cenário TUM-VT (clonado em .tools/ingolstadt por
scripts/fetch_ingolstadt_scenario.py ou git clone) com network_discovery a
auto-descobrir os 123 TLS. Materializa uma janela do dia escolhido num diretório
de trabalho limpo (.tools/ingol_run, git-ignored — sem espaços nem '---' que
quebram as CLI do SUMO), depois corre baseline-vs-TSP.

Não fabrica procura: usa as rotas motorizadas calibradas por detetores e o GTFS
real de autocarros do dia. As situações operacionais emergem da simulação.

Exemplos:
  python scripts/run_ingolstadt_demo.py --steps 300                 # 07:00, 5 min, TSP
  python scripts/run_ingolstadt_demo.py --begin 07:00:00 --steps 600
  python scripts/run_ingolstadt_demo.py --no-actuation              # braço baseline (sem atuação)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402

SCENARIO_DIR = ROOT / ".tools" / "ingolstadt" / "simulation" / "Ingolstadt SUMO 365"
WORK = ROOT / ".tools" / "ingol_run"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", default="2023-07-04", help="Dia-demo (Routes/TL/PT têm de existir no cenário).")
    p.add_argument("--begin", default="07:00:00", help="Início da janela (HH:MM:SS).")
    p.add_argument("--steps", type=int, default=300, help="Passos de simulação (step-length 1s).")
    p.add_argument("--no-actuation", action="store_true", help="Braço baseline: decide mas não atua.")
    p.add_argument("--refresh", action="store_true", help="Recopiar os ficheiros do cenário (lento).")
    p.add_argument("--config", default="configs/cits_ingolstadt_config.json", type=Path)
    p.add_argument("--tsp-config", default="configs/tsp_safety_config.json", type=Path)
    return p.parse_args()


def materialize(day: str, begin: str, refresh: bool) -> tuple[Path, Path]:
    """Copia (idempotente) os ficheiros do dia para um diretório limpo e escreve o sumocfg."""
    if not SCENARIO_DIR.exists():
        raise SystemExit(
            f"Cenário não encontrado em {SCENARIO_DIR}.\n"
            "Clona o TUM-VT primeiro: git clone --depth 1 "
            "https://github.com/TUM-VT/sumo_ingolstadt.git .tools/ingolstadt"
        )
    files = {
        "ingolstadt_net.net.xml": "ingolstadt_net.net.xml",
        f"Routes/routes_{day}_24h_det_calib.rou.xml.gz": f"Routes/routes_{day}_24h_det_calib.rou.xml.gz",
        f"TL/{day}_tlLogics_24h.tll.xml": f"TL/{day}_tlLogics_24h.tll.xml",
        f"TL/{day}_WAUT.xml": f"TL/{day}_WAUT.xml",
        "PT/pt_stops.add.xml": "PT/pt_stops.add.xml",
        f"PT/{day}_gtfs_trips.rou.xml": f"PT/{day}_gtfs_trips.rou.xml",
    }
    (WORK / "Routes").mkdir(parents=True, exist_ok=True)
    (WORK / "TL").mkdir(parents=True, exist_ok=True)
    (WORK / "PT").mkdir(parents=True, exist_ok=True)
    (WORK / "out").mkdir(parents=True, exist_ok=True)
    for src_rel, dst_rel in files.items():
        src, dst = SCENARIO_DIR / src_rel, WORK / dst_rel
        if not src.exists():
            raise SystemExit(f"Ficheiro do cenário em falta para o dia {day}: {src}")
        if refresh or not dst.exists():
            shutil.copy2(src, dst)
    sumocfg = WORK / "demo.sumocfg"
    sumocfg.write_text(
        f"""<configuration>
  <input>
    <net-file value="ingolstadt_net.net.xml"/>
    <route-files value="Routes/routes_{day}_24h_det_calib.rou.xml.gz"/>
    <additional-files value="TL/{day}_tlLogics_24h.tll.xml, TL/{day}_WAUT.xml, PT/pt_stops.add.xml, PT/{day}_gtfs_trips.rou.xml"/>
  </input>
  <time>
    <begin value="{begin}"/>
    <end value="24:00:00"/>
  </time>
  <processing>
    <step-length value="1"/>
    <ignore-junction-blocker value="15"/>
    <time-to-teleport value="240"/>
    <max-depart-delay value="100"/>
    <device.rerouting.probability value="0.7"/>
  </processing>
</configuration>
""",
        encoding="utf-8",
    )
    return sumocfg, WORK / "ingolstadt_net.net.xml"


def main() -> int:
    args = parse_args()
    sumocfg, net = materialize(args.day, args.begin, args.refresh)
    out = WORK / "out"

    cits = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    cits["sumo"]["sumocfg"] = str(sumocfg)
    cits["sumo"]["network"] = str(net)
    # schedule_plan GTFS: aponta os tempos `until` reais do dia materializado.
    schedule_plan = cits.get("schedule_plan", {})
    if isinstance(schedule_plan, dict) and str(schedule_plan.get("mode", "")).lower() == "gtfs":
        schedule_plan["gtfs_trips"] = str(WORK / "PT" / f"{args.day}_gtfs_trips.rou.xml")
        schedule_plan["pt_stops"] = str(WORK / "PT" / "pt_stops.add.xml")
    cits["logging"] = {
        "message_log": str(out / "cits_messages.jsonl"),
        "summary_report": str(out / "cits_summary.json"),
        "mapem_snapshot": str(out / "mapem.json"),
        "spatem_snapshot": str(out / "spatem.json"),
    }
    cits_path = WORK / "cits_resolved.json"
    cits_path.write_text(json.dumps(cits), encoding="utf-8")

    tsp = json.loads((ROOT / args.tsp_config).read_text(encoding="utf-8"))
    tsp["scenario_id"] = "ingolstadt_citywide_tsp"
    tsp.setdefault("logging", {}).update(
        {
            "decision_log": str(out / "tsp_decisions.jsonl"),
            "actuation_log": str(out / "tsp_actuation.jsonl"),
            "summary_report": str(out / "tsp_summary.json"),
        }
    )
    tsp_path = WORK / "tsp_resolved.json"
    tsp_path.write_text(json.dumps(tsp), encoding="utf-8")

    cits_config = load_cits_config(cits_path, root=ROOT)
    tsp_config = load_tsp_config(tsp_path, root=ROOT)

    n_tls = len(cits_config.signal_controlled_intersections)
    print(f"[setup] dia={args.day} begin={args.begin} steps={args.steps} "
          f"actuação={'OFF (baseline)' if args.no_actuation else 'ON'}")
    print(f"[setup] TLS sinal-controlados auto-descobertos: {n_tls}")

    controller = TSPControlController(cits_config, tsp_config)
    if controller.network_binding is not None:
        rep = controller.network_binding.coverage_report()
        print(f"[setup] cobertura matriz de conflitos: {rep['coverage_fraction']:.1%} "
              f"({rep['groups_with_authoritative_conflicts']}/{rep['n_signal_groups']} groups)")

    summary = controller.run_with_sumo(
        steps=args.steps, sumo_binary="sumo", apply_actuation=not args.no_actuation
    )

    def count(p: Path) -> int:
        return sum(1 for _ in p.open()) if p.exists() else 0

    ver = summary.get("signal_program_verification", {})
    print("\n==================== RESULTADO ====================")
    print(f"passos:                 {summary.get('steps')}")
    print(f"TLS atuáveis/total:     {ver.get('tls_actuable')}/{ver.get('tls_total')} "
          f"({ver.get('tls_fail_closed')} fail-closed)")
    print(f"decisões totais:        {summary.get('total_decisions')}")
    print(f"  aprovadas:            {summary.get('approved_decisions')}")
    print(f"  bloqueadas (safety):  {summary.get('blocked_by_safety')}")
    print(f"  green extension:      {summary.get('green_extension_decisions')}")
    print(f"  early green:          {summary.get('early_green_decisions')}")
    print(f"atuações aplicadas:     {summary.get('applied_events')} "
          f"(eventos TraCI reais: {summary.get('real_traci_applied_events')})")
    print(f"SREM gerados:           {count(out / 'cits_messages.jsonl') and summary.get('cits_by_type', {}).get('SREM', '?')}")
    print(f"C-ITS rejeitados:       {summary.get('cits_rejected_messages')}")
    print(f"logs em:                {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
