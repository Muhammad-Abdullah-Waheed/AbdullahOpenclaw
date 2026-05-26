"""Filesystem roots: installable package + repo root data (``.env``, ``default_workspace``)."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT: Path = Path(__file__).resolve().parent
"""Directory containing ``abdullah_openclaw`` package modules."""

REPO_ROOT: Path = PACKAGE_ROOT.parent
"""Project/checkout root — holds ``default_workspace/``, ``.env``, ``pyproject.toml``."""
