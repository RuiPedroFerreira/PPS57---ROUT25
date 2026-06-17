#!/usr/bin/env python3
"""Helpers genéricos partilhados pelos pacotes pps57_*.

Vive em `pps57_cits` por ser o pacote base (tsp e opt dependem dele), mas o
conteúdo é util genérico — não específico de C-ITS.
"""

from __future__ import annotations

from typing import Any


def optional_int(value: Any) -> int | None:
    """Converte `value` para int, devolvendo None se não for convertível.

    Substitui as seis cópias idênticas de `_optional_int` que existiam
    espalhadas por pps57_tsp e pps57_opt.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
