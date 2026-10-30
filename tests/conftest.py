"""Shared test helpers.

The scripts under ``tools/`` are dev utilities, not an installed package, so
tests load them by path through the ``tool_loader`` fixture.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_tool(name: str):
    path = _ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tools_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def tool_loader():
    return _load_tool
