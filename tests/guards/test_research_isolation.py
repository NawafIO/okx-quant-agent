"""Guard: research and strategy code reach market data only through the sealed loader.

The holdout is enforced in :mod:`okxq.backtest.holdout`. Python cannot stop a module from
opening Parquet itself, so this tripwire statically rejects the side doors in any research or
strategy module (Chief Advisor findings A-11 and checkpoint-3 #5). It is a tripwire, not a
sandbox: the audit chain is the evidence.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

SRC = Path(__file__).resolve().parents[2] / "src" / "okxq"
GUARDED_PACKAGES = ("strategy", "strategies", "research", "analysis")
#: Strategy code that already exists. Listed so the guard is never vacuous.
GUARDED_FILES = ("backtest/reference_strategies.py",)
FORBIDDEN_MODULES = ("duckdb", "pyarrow", "polars", "okxq.data", "sqlite3", "importlib")
FORBIDDEN_NAMES = {
    "okxq.backtest.holdout": {
        "_read_bars",
        "_read_funding",
        "load_calibration_bars",
        "load_calibration_funding",
        "load_holdout_bars",
        "load_holdout_funding",
        "unseal_holdout",
    },
    # Importing the whole module hands out every side door as an attribute.
    "okxq.backtest": {"holdout", "trials"},
    # Constructing gates is pinned anyway; importing the class has no research use.
    "okxq.backtest.gates": {"FrozenGates", "PINNED_GATES_SHA256"},
    # Regime labels come only from the pinned classify(); thresholds are not a strategy knob.
    "okxq.analysis.regime": {"classify_with", "RegimeParams", "FrozenRegime"},
}
WHOLE_MODULE_FORBIDDEN = {"okxq.backtest.holdout", "okxq.backtest.trials"}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"__import__", "exec", "eval"}:
                found.append(f"{node.func.id}()")
            continue
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
            found.extend(m for m in mods if m in WHOLE_MODULE_FORBIDDEN)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
            for name in FORBIDDEN_NAMES.get(node.module, ()):
                if any(a.name == name for a in node.names):
                    found.append(f"{node.module}.{name}")
        else:
            continue
        for mod in mods:
            if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN_MODULES):
                found.append(mod)
    return found


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import duckdb", ["duckdb"]),
        ("import pyarrow.parquet as pq", ["pyarrow.parquet"]),
        ("from okxq.data.store import ParquetStore", ["okxq.data.store"]),
        (
            "from okxq.backtest.holdout import unseal_holdout",
            ["okxq.backtest.holdout.unseal_holdout"],
        ),
        ("import okxq.backtest.holdout", ["okxq.backtest.holdout"]),
        ("from okxq.backtest import holdout", ["okxq.backtest.holdout"]),
        ("import importlib", ["importlib"]),
        ("from importlib import import_module", ["importlib"]),
        ("m = __import__('duckdb')", ["__import__()"]),
        ("from okxq.backtest.gates import FrozenGates", ["okxq.backtest.gates.FrozenGates"]),
        ("from okxq.backtest.holdout import load_research_bars", []),
        ("from okxq.backtest.gates import FROZEN", []),
        ("import numpy as np", []),
    ],
)
def test_the_scanner_catches_each_side_door(source: str, expected: list[str]) -> None:
    assert violations(source) == expected


def guarded_files() -> list[Path]:
    files = [SRC / f for f in GUARDED_FILES]
    for pkg in GUARDED_PACKAGES:
        if (SRC / pkg).exists():
            files.extend(sorted((SRC / pkg).rglob("*.py")))
    return files


def test_no_research_or_strategy_module_opens_data_directly() -> None:
    files = guarded_files()
    assert files, "guard scanned nothing - it would pass vacuously"
    assert all(f.exists() for f in files)
    offenders = {
        str(f.relative_to(SRC)): bad
        for f in files
        if (bad := violations(f.read_text(encoding="utf-8")))
    }
    assert offenders == {}
