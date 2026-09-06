"""Test setup that keeps Home Assistant out of lightweight protocol tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[1]

custom_components = ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

paseo = ModuleType("custom_components.paseo")
paseo.__path__ = [str(ROOT / "custom_components" / "paseo")]
sys.modules.setdefault("custom_components.paseo", paseo)
