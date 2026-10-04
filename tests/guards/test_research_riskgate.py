"""Guards for the cycle-2 research risk gate (okxq/backtest/riskgate.py), the condition under
which it may apply portfolio events (tests/guards/test_risk_enforcement.py, APPLY_ALLOWED).

* It touches no persistence: no import of the store, kill switch, cycle.ingest, the audit
  chain or sqlite3, and no file is opened or written.
* Only okxq/backtest and scripts import it.
* ``apply`` is called exactly once, inside ``_apply``, which then calls ``halt_triggers``.
* OBSERVE_HALTS (records halts without blocking) appears only in the gate and the baseline
  script; research trials (walkforward) can never select it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
OKXQ = ROOT / "src" / "okxq"
GATE = OKXQ / "backtest" / "riskgate.py"
BASELINE_SCRIPT = "scripts/c2_baseline.py"


def tree() -> ast.Module:
    if not GATE.exists():
        pytest.skip("research gate not present")
    return ast.parse(GATE.read_text(encoding="utf-8"))


def test_the_gate_imports_no_persistence() -> None:
    banned_modules = {"okxq.risk.store", "okxq.risk.killswitch", "okxq.audit.chain", "sqlite3"}
    bad = []
    for n in ast.walk(tree()):
        if isinstance(n, ast.Import):
            bad += [a.name for a in n.names if a.name in banned_modules or a.name == "okxq.audit"]
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if mod in banned_modules or mod.startswith("okxq.audit"):
                bad.append(mod)
            if mod == "okxq.risk" and any(a.name in {"store", "killswitch"} for a in n.names):
                bad.append(f"{mod}.store/killswitch")
            if mod == "okxq.risk.cycle" and any(a.name == "ingest" for a in n.names):
                bad.append("cycle.ingest")
    assert bad == []


def test_the_gate_opens_and_writes_no_file() -> None:
    writes = {"open", "write_text", "write_bytes", "touch", "mkdir", "unlink", "rename", "replace"}
    bad = []
    for n in ast.walk(tree()):
        if isinstance(n, ast.Call):
            f = n.func
            name = (
                f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            )
            # dataclasses.replace is the one permitted 'replace' (records, not files).
            if name in writes and not (name == "replace" and isinstance(f, ast.Name)):
                bad.append(f"{name}@{n.lineno}")
    assert bad == []


def test_only_backtest_and_scripts_import_the_gate() -> None:
    bad = []
    for path in sorted(OKXQ.rglob("*.py")):
        rel = path.relative_to(OKXQ).as_posix()
        if rel.startswith("backtest/"):
            continue
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("riskgate"):
                bad.append(rel)
            if isinstance(n, ast.Import) and any(a.name.endswith("riskgate") for a in n.names):
                bad.append(rel)
    assert bad == []


def test_apply_is_called_once_inside_apply_and_followed_by_the_halts() -> None:
    t = tree()
    calls = [
        n
        for n in ast.walk(t)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "apply"
    ]
    assert len(calls) == 1
    method = next(n for n in ast.walk(t) if isinstance(n, ast.FunctionDef) and n.name == "_apply")
    inside = [n for n in ast.walk(method) if n in calls]
    assert inside == calls
    names = [
        n.func.id
        for n in ast.walk(method)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert names.index("halt_triggers") > names.index("apply")


def test_observe_halts_is_confined_to_the_gate_and_the_baseline_script() -> None:
    tree()  # skip when absent
    bad = []
    for path in sorted([*OKXQ.rglob("*.py"), *(ROOT / "scripts").glob("*.py")]):
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"src/okxq/backtest/riskgate.py", BASELINE_SCRIPT}:
            continue
        if "OBSERVE_HALTS" in path.read_text(encoding="utf-8"):
            bad.append(rel)
    assert bad == []
