#!/usr/bin/env python3
"""Run the PPS57 local FastAPI control plane."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arranca a API local de controlo da plataforma PPS57.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("FastAPI/uvicorn não instalados. Executa: pip install -r requirements.txt") from exc

    args = parse_args()
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
