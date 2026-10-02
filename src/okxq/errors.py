"""Typed error taxonomy.

Retry policy and halt behaviour depend entirely on correct error classification, so errors
are types rather than strings. Anything safety-relevant derives from :class:`SafetyError`,
which the master agent treats as a transition to ``HALTED`` rather than something to retry.
"""


class OkxqError(Exception):
    """Base class for every error this system raises deliberately."""


class SafetyError(OkxqError):
    """A safety invariant was violated. Never retried, never swallowed.

    The master agent's handling of any ``SafetyError`` is to halt. There is no code path
    that catches one and continues trading.
    """


class LiveTradingLockedError(SafetyError):
    """Raised on any attempt to construct or reach the LIVE environment while locked.

    LIVE lock Layer 2 (architecture §6.4). See :mod:`okxq.phase`.
    """


class EnvironmentMismatchError(SafetyError):
    """A record's environment did not match the environment of the process handling it.

    Contract rule D-2 (architecture §4). This is deliberately fatal rather than coerced:
    silently accepting a foreign-environment record is the failure mode that ends in a
    real order being placed from a simulated run.
    """


class ContractViolationError(OkxqError):
    """A data contract was violated - malformed, missing or out-of-range field."""


class AuditChainError(SafetyError):
    """The audit hash chain failed verification: tampered, truncated or reordered."""


class ConfigError(OkxqError):
    """Invalid or missing configuration."""
