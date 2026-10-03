"""Guard: nothing in okxq builds or edits a contract record without validation (M5, B-3).

pydantic's ``model_construct``, ``model_copy(update=...)``, the deprecated
``copy(update=...)`` and ``object.__setattr__`` all skip validators - including the
TradeProposal rule that an APPROVED verdict cannot carry a failed risk check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

SRC = Path(__file__).resolve().parents[2] / "src" / "okxq"


def violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "model_construct":
            found.append("model_construct")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            if f.attr in {"model_copy", "copy"} and any(k.arg == "update" for k in node.keywords):
                found.append(f"{f.attr}(update=...)")
            if f.attr == "__setattr__" and isinstance(f.value, ast.Name) and f.value.id == "object":
                found.append("object.__setattr__")
    return found


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("TradeProposal.model_construct(verdict='APPROVED')", ["model_construct"]),
        ("p.model_copy(update={'verdict': 'APPROVED'})", ["model_copy(update=...)"]),
        ("p.copy(update={'qty_base': 1})", ["copy(update=...)"]),
        ("object.__setattr__(p, 'verdict', 'APPROVED')", ["object.__setattr__"]),
        ("p.model_copy()", []),
        ("d.copy()", []),
    ],
)
def test_detector(source: str, expected: list[str]) -> None:
    assert violations(source) == expected


def test_no_validation_bypass_anywhere_in_okxq() -> None:
    bad = {
        str(p.relative_to(SRC)): v
        for p in sorted(SRC.rglob("*.py"))
        if (v := violations(p.read_text(encoding="utf-8")))
    }
    assert bad == {}
