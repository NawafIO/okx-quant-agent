"""Guard: M5's acceptance criteria cannot be met on paper while failing in fact.

* 100% branch coverage of okxq.risk runs under a config with NO exclusions, pinned here,
  and CI / scripts/verify.ps1 invoke it with --cov-branch --cov-fail-under=100 (pinned) -
  verify.ps1's -SkipCoverage cannot skip it.
* The risk package contains nothing that hides code from coverage or vanishes under -O:
  no assert, no pragma, no type: ignore, no noqa, no TYPE_CHECKING.
* No bypass: no environment variable is read, no network module is imported, the battery
  is read-only and covers the pinned check set.
* R-4: LLM-sourced fields are referenced only by qualitative.py (and declared in inputs.py).
* Nothing outside the kill-switch module references ``disarm``: it is never automatic.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from pathlib import Path
from types import MappingProxyType

import coverage
import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
RISK = ROOT / "src" / "okxq" / "risk"
OKXQ = ROOT / "src" / "okxq"

#: SHA-256 of .coveragerc-risk, frozen at M5 (2026-10-03).
COVERAGERC_SHA256 = "c8bbc0b6e3ee9da74113cc1bcafa23f1b2e5203d523e3b608884b54b83ba1302"
RISK_COVERAGE_ARGS = (
    "tests/unit/risk tests/guards/test_risk_policy_frozen.py -p no:cacheprovider "
    "--cov=okxq.risk --cov-config=.coveragerc-risk --cov-branch --cov-fail-under=100"
)


def risk_sources() -> list[Path]:
    files = sorted(RISK.glob("*.py"))
    assert len(files) >= 12, "guard is vacuous: risk package not found"
    return files


# --- coverage --------------------------------------------------------------------------------


def _lf_sha256(raw: bytes) -> str:
    """Hash with LF line endings: git checks text out as CRLF on Windows, which would
    change the bytes but not the content being pinned."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def test_the_pin_holds_on_a_windows_crlf_checkout() -> None:
    raw = (ROOT / ".coveragerc-risk").read_bytes().replace(b"\r\n", b"\n")
    assert _lf_sha256(raw.replace(b"\n", b"\r\n")) == _lf_sha256(raw) == COVERAGERC_SHA256
    assert _lf_sha256(raw + b"exclude_lines = pragma\n") != COVERAGERC_SHA256  # still bites


def test_the_risk_coverage_config_is_pinned_and_excludes_nothing() -> None:
    raw = (ROOT / ".coveragerc-risk").read_bytes()
    assert _lf_sha256(raw) == COVERAGERC_SHA256
    cfg = coverage.Coverage(config_file=str(ROOT / ".coveragerc-risk")).config
    assert cfg.exclude_list == []
    assert cfg.partial_list == []
    assert cfg.partial_always_list == []
    assert not cfg.run_omit and not cfg.report_omit
    assert cfg.branch is True and cfg.fail_under == 100
    assert cfg.source == ["okxq.risk"]


def test_ci_runs_the_risk_coverage_gate_exactly() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ci = ci.replace("\r\n", "\n")
    assert f"run: uv run pytest {RISK_COVERAGE_ARGS} --cov-report=term-missing" in ci


def test_verify_ps1_runs_the_gate_and_skipcoverage_cannot_skip_it() -> None:
    # .gitattributes checks .ps1 out with CRLF on Windows, where verify.ps1 actually runs.
    ps = (ROOT / "scripts" / "verify.ps1").read_text(encoding="utf-8").replace("\r\n", "\n")
    args = ", ".join(f"'{a}'" for a in ["-m", "pytest", *RISK_COVERAGE_ARGS.split()])
    assert args in ps
    skip_block_end = ps.index("\n}\n", ps.index("if ($SkipCoverage)"))
    assert ps.index(args) > skip_block_end, "the risk gate must sit outside -SkipCoverage"


# --- nothing hides code from coverage or vanishes under -O ---------------------------------


def _comments(src: str) -> list[str]:
    return [
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type == tokenize.COMMENT
    ]


@pytest.mark.parametrize("path", risk_sources(), ids=lambda p: p.name)
def test_risk_module_has_no_assert_pragma_ignore_or_type_checking(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    asserts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    tc = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Name | ast.Attribute)
        and (getattr(n, "id", None) or getattr(n, "attr", None)) == "TYPE_CHECKING"
    ]
    bad_comments = [
        c for c in _comments(src) if any(w in c.lower() for w in ("pragma", "type: ignore", "noqa"))
    ]
    assert (asserts, tc, bad_comments) == ([], [], [])


# --- no bypass -------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = (
    "ccxt",
    "requests",
    "socket",
    "urllib",
    "http",
    "subprocess",
    "okxq.data",
    "okxq.backtest",
    "okxq.analysis.regime",
)


@pytest.mark.parametrize("path", risk_sources(), ids=lambda p: p.name)
def test_risk_module_reads_no_environment_and_imports_no_network(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    env = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr in {"environ", "getenv", "getenvb"}
    ]
    mods: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.append(n.module)
    bad = [m for m in mods if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_IMPORTS)]
    assert (env, bad) == ([], [])


def test_the_battery_is_read_only_and_covers_the_pinned_checks() -> None:
    from okxq.risk.checks import BATTERY
    from okxq.risk.policy import REQUIRED_CHECKS

    assert isinstance(BATTERY, MappingProxyType)
    sizing = {c for c in REQUIRED_CHECKS if c.startswith("SZ-")}
    assert set(BATTERY) | sizing == set(REQUIRED_CHECKS)
    assert len(REQUIRED_CHECKS) == len(set(REQUIRED_CHECKS))


# --- R-4: LLM-sourced fields stay in qualitative.py ------------------------------------------

LLM_NAMES = {
    "SentimentScore",
    "RegimeLabel",
    "conviction",
    "score",
    "confidence",
    "determined_by",
    "sentiment",
    "regime",
}
ALLOWED_R4 = {"qualitative.py", "inputs.py"}


@pytest.mark.parametrize(
    "path", [p for p in risk_sources() if p.name not in ALLOWED_R4], ids=lambda p: p.name
)
def test_r4_no_llm_sourced_field_outside_the_qualitative_gate(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for n in ast.walk(tree):
        name = (
            getattr(n, "id", None)
            if isinstance(n, ast.Name)
            else getattr(n, "attr", None)
            if isinstance(n, ast.Attribute)
            else None
        )
        if name in LLM_NAMES:
            hits.append((getattr(n, "lineno", 0), name))
        if isinstance(n, ast.ImportFrom):
            hits += [(n.lineno, a.name) for a in n.names if a.name in LLM_NAMES]
        if isinstance(n, ast.Constant) and n.value in LLM_NAMES:
            hits.append((n.lineno, n.value))
    assert hits == []


# --- disarm is never automatic ---------------------------------------------------------------


def test_nothing_outside_the_kill_switch_references_disarm() -> None:
    offenders = []
    for path in sorted(OKXQ.rglob("*.py")):
        if path.name == "killswitch.py":
            continue
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Attribute) and n.attr == "disarm") or (
                isinstance(n, ast.Constant) and n.value == "disarm"
            ):
                offenders.append(f"{path.relative_to(OKXQ)}:{n.lineno}")
    assert offenders == []


# --- one ingest path (closing audit #4) ------------------------------------------------------


def test_only_the_store_applies_events_and_only_cycle_ingests() -> None:
    """Nothing persists or applies a portfolio event except store.py (called through
    cycle.ingest, which latches halts). A name-level tripwire: it cannot see a store passed
    in as an untyped object, which is why ``ingest`` is the documented single entry."""
    offenders = []
    for path in sorted(OKXQ.rglob("*.py")):
        rel = path.relative_to(OKXQ).as_posix()
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.ImportFrom)
                and n.module == "okxq.risk.portfolio"
                and any(a.name == "apply" for a in n.names)
                and rel != "risk/store.py"
            ):
                offenders.append(f"{rel}: imports apply")
            if isinstance(n, ast.Name | ast.Attribute):
                name = getattr(n, "id", None) or getattr(n, "attr", None)
                if name == "PortfolioStore" and rel != "risk/store.py":
                    offenders.append(f"{rel}: references PortfolioStore")
    assert offenders == []
