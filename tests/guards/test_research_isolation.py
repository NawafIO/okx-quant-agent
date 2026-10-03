"""Guard: research and strategy code reach market data only through the sealed loader.

The holdout is enforced in :mod:`okxq.backtest.holdout`. Python cannot stop a module from
opening Parquet itself, so this tripwire statically rejects the obvious side doors in any
research/strategy package (Chief Advisor finding A-11).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

SRC = Path(__file__).resolve().parents[2] / "src" / "okxq"
GUARDED_PACKAGES = ("strategy", "strategies", "research")
FORBIDDEN_MODULES = ("duckdb", "pyarrow", "polars", "okxq.data", "sqlite3")
FORBIDDEN_NAMES = {
    "okxq.backtest.holdout": {
        "_read_bars",
        "_read_funding",
        "load_calibration_bars",
        "load_calibration_funding",
        "load_holdout_bars",
        "load_holdout_funding",
        "unseal_holdout",
    }
}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
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
            if mod == "okxq.backtest.holdout" and isinstance(node, ast.Import):
                found.append(mod)  # whole-module import would expose the side doors
    return found


def test_the_scanner_catches_each_side_door() -> None:
    assert violations("import duckdb") == ["duckdb"]
    assert violations("import pyarrow.parquet as pq") == ["pyarrow.parquet"]
    assert violations("from okxq.data.store import ParquetStore") == ["okxq.data.store"]
    assert violations("from okxq.backtest.holdout import unseal_holdout") == [
        "okxq.backtest.holdout.unseal_holdout"
    ]
    assert violations("import okxq.backtest.holdout") == ["okxq.backtest.holdout"]
    assert violations("from okxq.backtest.holdout import load_research_bars") == []
    assert violations("import numpy as np") == []


def test_no_research_or_strategy_module_opens_data_directly() -> None:
    offenders = {}
    for pkg in GUARDED_PACKAGES:
        for path in sorted((SRC / pkg).rglob("*.py")) if (SRC / pkg).exists() else []:
            bad = violations(path.read_text(encoding="utf-8"))
            if bad:
                offenders[str(path.relative_to(SRC))] = bad
    assert offenders == {}
