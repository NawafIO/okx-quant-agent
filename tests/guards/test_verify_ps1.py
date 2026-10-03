"""Guard: the Windows verification gate actually runs its checks.

Until 2026-10-03 ``Invoke-Step`` in scripts/verify.ps1 named its argument list ``$Args`` - a
PowerShell AUTOMATIC variable - so every step splatted an EMPTY array and started python.exe
as an interactive REPL. Typing exit() returned 0, so each "check" passed without running.

* Static: no parameter in any scripts/*.ps1 is named after an automatic variable, and
  Invoke-Step refuses to launch python with no arguments.
* Dynamic (runs wherever ``pwsh`` exists - GitHub's ubuntu runners have it): the REAL
  verify.ps1 runs against a stand-in interpreter that logs its argv; every step must receive
  its full argument list, a failing step must stop the gate with its exit code, and
  -SkipCoverage must not skip the M5 risk gate.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "scripts").glob("*.ps1"))

#: PowerShell automatic variables (about_Automatic_Variables) - never a parameter name.
AUTOMATIC = {
    "args",
    "input",
    "this",
    "psitem",
    "_",
    "error",
    "host",
    "home",
    "pid",
    "matches",
    "myinvocation",
    "pscmdlet",
    "psboundparameters",
    "true",
    "false",
    "null",
    "lastexitcode",
    "foreach",
    "switch",
    "event",
    "eventargs",
    "eventsubscriber",
    "sender",
    "ofs",
    "profile",
    "pwd",
    "shellid",
    "stacktrace",
    "executioncontext",
    "nestedpromptlevel",
    "psscriptroot",
    "pscommandpath",
    "psculture",
    "psuiculture",
    "psversiontable",
    "pshome",
    "isglobal",
    "iscoreclr",
    "islinux",
    "ismacos",
    "iswindows",
    "consolefilename",
    "psdebugcontext",
    "pssenderinfo",
    "enabledexperimentalfeatures",
}


def _param_names(text: str) -> list[str]:
    """Every $name declared inside a param( ... ) block, nested parentheses included."""
    names: list[str] = []
    for m in re.finditer(r"\bparam\s*\(", text, flags=re.IGNORECASE):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        names += re.findall(r"\$(\w+)", text[m.end() : i - 1])
    return names


def test_scripts_exist() -> None:
    assert {p.name for p in SCRIPTS} >= {"verify.ps1", "archive_funding.ps1"}


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_parameter_is_named_after_an_automatic_variable(path: Path) -> None:
    names = _param_names(path.read_text(encoding="utf-8"))
    assert names, "guard is vacuous: no param block found"
    assert [n for n in names if n.lower() in AUTOMATIC] == []


def test_the_detector_catches_the_original_bug() -> None:
    assert _param_names("function F { param([string]$Name, [string[]]$Args) }") == [
        "Name",
        "Args",
    ]


def test_invoke_step_refuses_an_empty_argument_list() -> None:
    text = (ROOT / "scripts" / "verify.ps1").read_text(encoding="utf-8")
    assert "if (-not $PyArgs -or $PyArgs.Count -eq 0) {" in text
    assert "& $py @PyArgs" in text


# --- the real script, end to end ------------------------------------------------------------

PWSH = shutil.which("pwsh")
SHIM = """#!/bin/bash
printf '%s\\n' "$*" >> "$LOG"
if [ "$#" -eq 0 ]; then exit 99; fi
if [ -n "$FAIL_ON" ] && [[ "$*" == *"$FAIL_ON"* ]]; then exit 3; fi
exit 0
"""
EXPECTED = [
    "-m ruff check .",
    "-m ruff format --check .",
    "-m mypy",
    "-m pytest -m guard -v",
    "-m pytest --cov --cov-report=term-missing",
    "-m pytest tests/unit/risk tests/guards/test_risk_policy_frozen.py -p no:cacheprovider "
    "--cov=okxq.risk --cov-config=.coveragerc-risk --cov-branch --cov-fail-under=100 "
    "--cov-report=term-missing",
]


def _run(tmp_path: Path, *extra: str, fail_on: str = "") -> tuple[int, list[str]]:
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy(ROOT / "scripts" / "verify.ps1", tmp_path / "scripts" / "verify.ps1")
    venv = tmp_path / ".venv" / "Scripts"
    venv.mkdir(parents=True, exist_ok=True)
    shim = venv / "python.exe"
    shim.write_text(SHIM)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / f"argv{fail_on or ''}{''.join(extra)}.log"
    env = os.environ | {"LOG": str(log), "FAIL_ON": fail_on}
    assert PWSH is not None
    proc = subprocess.run(  # noqa: S603 - fixed argv: the repository's own script
        [PWSH, "-NoProfile", "-File", str(tmp_path / "scripts" / "verify.ps1"), *extra],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    return proc.returncode, log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="needs pwsh and a bash shim")
def test_verify_ps1_runs_every_check_with_its_arguments(tmp_path: Path) -> None:
    code, argv = _run(tmp_path)
    assert code == 0
    assert argv == EXPECTED  # no empty argv: python was never started as a REPL


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="needs pwsh and a bash shim")
def test_a_failing_check_stops_the_gate_with_its_exit_code(tmp_path: Path) -> None:
    code, argv = _run(tmp_path, fail_on="mypy")
    assert code == 3
    assert argv == EXPECTED[:3]


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="needs pwsh and a bash shim")
def test_skipcoverage_cannot_skip_the_risk_gate(tmp_path: Path) -> None:
    code, argv = _run(tmp_path, "-SkipCoverage")
    assert code == 0
    assert argv[-1] == EXPECTED[-1] and "-m pytest" in argv
