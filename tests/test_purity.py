"""Architectural guard: the pure core must never import Home Assistant.

Per AGENTS.md, detection and the models are pure Python (LLM-free, HA-free) so
they stay deterministic and testable. Function-local HA imports in the
integration adapter are fine; module-level ones are not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "hal1976"

# Files that must remain HA-free at module level. config_flow.py is the
# HA-facing surface and is deliberately excluded.
PURE_MODULES = [
    "__init__.py",
    "const.py",
    "models.py",
    "detect.py",
    "yaml_store.py",
    "seed.py",
    "analyser.py",
    "analysis.py",
    "notifier.py",
    "websocket_api.py",
]

MODULE_LEVEL_HA_IMPORT = re.compile(r"^(?:from|import)\s+homeassistant", re.MULTILINE)


@pytest.mark.parametrize("filename", PURE_MODULES)
def test_core_module_has_no_module_level_ha_import(filename: str) -> None:
    source = (PKG / filename).read_text(encoding="utf-8")
    match = MODULE_LEVEL_HA_IMPORT.search(source)
    assert match is None, (
        f"{filename} imports Home Assistant at module level: {match.group(0)!r}. "
        "Keep core pure — move HA usage into the adapter or import it inside a function."
    )
