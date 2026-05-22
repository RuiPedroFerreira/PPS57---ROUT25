#!/usr/bin/env python3
"""Run the PPS57 local FastAPI control plane.

Modelo de ameaça
----------------
Os endpoints mutating (`/runs/start|stop|pause|resume`) **não têm autenticação**
— este servidor é assumido como local-only. O default `--host=127.0.0.1`
restringe o socket a loopback. Hosts não-loopback são bloqueados salvo opt-in
explícito com `--allow-non-loopback`.
"""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arranca a API local de controlo da plataforma PPS57.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1 / loopback only).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow binding the unauthenticated control API to a non-loopback address.",
    )
    return parser.parse_args()


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("FastAPI/uvicorn não instalados. Executa: pip install -r requirements.txt") from exc

    args = parse_args()
    if not _is_loopback(args.host) and not args.allow_non_loopback:
        print(
            f"[run_platform_api] BLOQUEADO: --host={args.host!r} não é loopback e "
            "os endpoints /runs/* não têm autenticação. Usa --host=127.0.0.1 "
            "ou passa --allow-non-loopback conscientemente após restringires a rede.",
            file=sys.stderr,
            flush=True,
        )
        return 2
    uvicorn.run(
        "pps57_platform.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(SRC),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
