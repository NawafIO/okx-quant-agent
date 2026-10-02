"""Process entry point. The environment is a required argument with no default.

    python -m okxq.run --env PAPER

M0 scope: validate the environment, create its directory tree, verify the audit chain, and
report. There is no trading loop yet - the master agent arrives with M5/M6, and nothing
order-capable exists until the Risk Engine is complete and property-tested.
"""

from __future__ import annotations

import argparse
import sys

from okxq.audit.chain import AuditChain, verify_chain
from okxq.env.profiles import EnvProfile, build_profile, ensure_dirs, parse_env
from okxq.errors import OkxqError
from okxq.obs.logging import configure_logging, get_logger
from okxq.phase import LIVE_UNLOCK_PHASE, PHASE

log = get_logger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="okxq",
        description="OKX quantitative trading system (M0 scaffold - no trading loop yet)",
    )
    # required=True and no default: there is deliberately no way to start without stating
    # the environment out loud (architecture §6.2).
    parser.add_argument(
        "--env",
        required=True,
        choices=["DEMO", "PAPER", "LIVE"],
        help="execution environment - required, no default",
    )
    return parser


def boot(env_raw: str) -> EnvProfile:
    """Validate the environment, prepare its tree, and verify its audit chain."""
    profile = build_profile(parse_env(env_raw))
    ensure_dirs(profile)

    verified = verify_chain(profile.audit_log)
    chain = AuditChain(profile.audit_log)
    chain.append(
        "boot",
        {
            "env": profile.env,
            "phase": PHASE,
            "can_place_orders": profile.can_place_orders,
            "real_capital_at_risk": profile.real_capital_at_risk,
            "prior_records_verified": verified,
        },
    )

    log.info(
        "boot complete",
        extra={
            "env": profile.env,
            "banner": profile.banner(),
            "phase": PHASE,
            "live_unlock_phase": LIVE_UNLOCK_PHASE,
            "can_place_orders": profile.can_place_orders,
            "credentials_present": profile.credentials is not None,
            "audit_records_verified": verified,
        },
    )
    return profile


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code rather than raising."""
    configure_logging()
    args = _parser().parse_args(argv)
    try:
        boot(args.env)
    except OkxqError as exc:
        # Expected, typed failures - including LiveTradingLockedError, which is the system
        # working correctly, not a crash. Report clearly and exit non-zero.
        log.error("boot refused", extra={"error": type(exc).__name__, "detail": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
