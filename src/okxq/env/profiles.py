"""Environment profiles - DEMO / PAPER / LIVE decoupling (architecture §6).

The environment is selected **once, at process start, from an explicit CLI argument**. There
is no default, no mutable global "current env", and no runtime switch. A single frozen
:class:`EnvProfile` is constructed at startup and injected everywhere.

Per ruling X-3 the three environments are genuinely different mechanisms, not one flag:

  DEMO   contacts OKX's simulated-trading venue over the real API. Validates the adapter,
         auth, symbol metadata, order lifecycle and error taxonomy.
  PAPER  never contacts an order endpoint at all. Our own fill engine consumes the live
         public feed. Validates strategy, slippage model and latency budget.
  LIVE   locked. See :func:`build_profile` and :mod:`okxq.phase`.

**Neither DEMO nor PAPER is evidence of profitability** - DEMO fills come from a simulator
whose liquidity does not reflect the real book, and PAPER fills come from our own model.

Physical isolation is per environment: separate SQLite file, Parquet root, audit chain and
log directory. A DEMO process cannot write to the PAPER ledger.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from okxq.contracts import Env
from okxq.errors import ConfigError, LiveTradingLockedError
from okxq.obs.secrets import register_secret
from okxq.phase import LIVE_UNLOCK_PHASE, PHASE, live_trading_unlocked

#: Repository root, derived from this file's location.
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class Credentials:
    """Venue API credentials. Never logged, never serialised, never shown to an LLM."""

    api_key: str
    api_secret: str
    passphrase: str

    def __repr__(self) -> str:
        """Redact on repr, so an accidental f-string or traceback cannot leak the key."""
        return "Credentials(api_key='***', api_secret='***', passphrase='***')"


@dataclass(frozen=True, slots=True)
class EnvProfile:
    """Immutable, per-environment configuration.

    Attributes:
        env: Which environment this process is bound to.
        can_place_orders: Whether this environment has any order endpoint at all. False for
            PAPER, which simulates fills locally and never calls one.
        use_sandbox: Whether the adapter should use OKX's simulated-trading mode. The exact
            mechanism is open question Q-2, to be verified empirically at M1/M6; it is
            confined to the adapter so that answering Q-2 is a one-place change.
        real_capital_at_risk: True only for LIVE. Used by the dashboard banner and by
            assertions that must not fire in simulation.
    """

    env: Env
    can_place_orders: bool
    use_sandbox: bool
    real_capital_at_risk: bool
    kill_switch_armed_by_default: bool
    state_db: Path
    parquet_root: Path
    audit_log: Path
    log_dir: Path
    dashboard_port: int
    credentials: Credentials | None

    def banner(self) -> str:
        """Operator-facing environment banner (architecture §19.1).

        Confusion about which environment is in front of the operator is itself a risk
        control, so this is rendered large and colour-coded on every dashboard page.
        """
        return {
            "DEMO": "DEMO - OKX simulated venue - NO REAL CAPITAL",
            "PAPER": "PAPER - local fill simulation - NO ORDERS SENT - NO REAL CAPITAL",
            "LIVE": "*** LIVE - REAL CAPITAL AT RISK ***",
        }[self.env]


def _credentials_from_env(prefix: str) -> Credentials | None:
    """Read credentials from environment variables, or return None if absent.

    Returning None rather than raising is deliberate: *absence of a credential is the
    normal, expected state* for LIVE in phases 1-3, and for PAPER always. Callers that
    genuinely need to place an order fail at that point, not at startup.
    """
    key = os.environ.get(f"{prefix}_API_KEY")
    secret = os.environ.get(f"{prefix}_API_SECRET")
    passphrase = os.environ.get(f"{prefix}_API_PASSPHRASE")
    if not (key and secret and passphrase):
        return None
    for value in (key, secret, passphrase):
        register_secret(value)
    return Credentials(api_key=key, api_secret=secret, passphrase=passphrase)


def _paths(env: Env, root: Path) -> dict[str, Path]:
    slug = env.lower()
    return {
        "state_db": root / "state" / slug / "okxq.db",
        "parquet_root": root / "data" / slug / "parquet",
        "audit_log": root / "audit" / slug / "audit.jsonl",
        "log_dir": root / "logs" / slug,
    }


def build_profile(env: Env, *, root: Path | None = None) -> EnvProfile:
    """Construct the profile for ``env``.

    The ``match`` has no default branch and ends in :func:`typing.assert_never`, so adding a
    fourth environment is a type error rather than a silent fallthrough, and a malformed
    value raises immediately.

    Raises:
        LiveTradingLockedError: when ``env`` is LIVE and the phase constant still locks it.
            This is LIVE lock Layer 2 (architecture §6.4). Layer 1 - no trade-permitted LIVE
            credential is ever provisioned - applies regardless and is the layer that cannot
            be defeated by a defect in this file.
        ConfigError: when ``env`` is not a recognised environment.
    """
    base = root if root is not None else PROJECT_ROOT
    paths = _paths(env, base)

    match env:
        case "DEMO":
            return EnvProfile(
                env="DEMO",
                can_place_orders=True,
                use_sandbox=True,
                real_capital_at_risk=False,
                kill_switch_armed_by_default=False,
                dashboard_port=8601,
                credentials=_credentials_from_env("OKXQ_DEMO"),
                **paths,
            )
        case "PAPER":
            return EnvProfile(
                env="PAPER",
                # PAPER never calls an order endpoint; fills are simulated locally (§6.3).
                can_place_orders=False,
                use_sandbox=True,
                real_capital_at_risk=False,
                kill_switch_armed_by_default=False,
                dashboard_port=8602,
                # Read-only at most: PAPER needs public market data only.
                credentials=_credentials_from_env("OKXQ_PAPER"),
                **paths,
            )
        case "LIVE":
            if not live_trading_unlocked():
                raise LiveTradingLockedError(
                    f"LIVE trading is locked: PHASE={PHASE} < LIVE_UNLOCK_PHASE="
                    f"{LIVE_UNLOCK_PHASE}. Unlocking requires every criterion in "
                    "architecture §11.5, a reviewed source change to okxq/phase.py, and "
                    "written human sign-off recorded in the audit trail. Note that "
                    "unlocking this layer alone still does not permit trading: no "
                    "trade-permitted LIVE credential is provisioned (lock Layer 1)."
                )
            return EnvProfile(  # pragma: no cover - unreachable while PHASE < 4
                env="LIVE",
                can_place_orders=True,
                use_sandbox=False,
                real_capital_at_risk=True,
                # Armed by default for LIVE: lock Layer 3 (architecture §6.4).
                kill_switch_armed_by_default=True,
                dashboard_port=8603,
                credentials=_credentials_from_env("OKXQ_LIVE"),
                **paths,
            )
        case _:
            assert_never(env)


def ensure_dirs(profile: EnvProfile) -> None:
    """Create the per-environment directory tree for ``profile``."""
    for path in (profile.state_db, profile.audit_log):
        path.parent.mkdir(parents=True, exist_ok=True)
    for directory in (profile.parquet_root, profile.log_dir):
        directory.mkdir(parents=True, exist_ok=True)


def parse_env(raw: str) -> Env:
    """Validate a raw CLI string as an environment name.

    Raises:
        ConfigError: if ``raw`` is not exactly one of the three names. Case-sensitive and
            unforgiving by intent - "live" must not silently become LIVE.
    """
    # Compared against literals individually so the type checker narrows the return type;
    # a membership test against a tuple does not narrow `str` to `Env`.
    if raw == "DEMO":
        return "DEMO"
    if raw == "PAPER":
        return "PAPER"
    if raw == "LIVE":
        return "LIVE"
    raise ConfigError(f"unknown environment {raw!r}; expected DEMO, PAPER or LIVE")
