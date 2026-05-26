#!/usr/bin/env python3
"""Dashboard template helpers for local scenario runs."""
from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
DASHBOARD_TEMPLATE = TEMPLATE_DIR / "dashboard.html"


def dashboard_html() -> str:
    return DASHBOARD_TEMPLATE.read_text(encoding="utf-8")


DASHBOARD_HTML = dashboard_html()
