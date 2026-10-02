"""Project phase constant - LIVE lock Layer 2 (architecture §6.4).

This module exists so that enabling LIVE trading is a reviewable source diff and not a
configuration edit. It deliberately has no dependencies, reads no environment variables,
and no file, so nothing at runtime can change its value.

LIVE lock layers, in order of strength:

  Layer 1  CREDENTIAL ABSENCE  - no trade-permitted LIVE key is ever provisioned.
                                 This is the layer that cannot be defeated by a bug
                                 in our own code.
  Layer 2  PHASE CONSTANT      - this module.
  Layer 3  KILL SWITCH         - armed by default for LIVE.
  Layer 4  CI GUARD            - tests/guards/ asserts LIVE is unreachable.
"""

from typing import Final

#: Current project phase. 1 = design, 2 = foundation/research, 3 = simulated operation,
#: 4 = live. Raising this is a deliberate, reviewed change - never automated.
PHASE: Final[int] = 2

#: The phase at which the LIVE environment stops raising. Promotion to LIVE additionally
#: requires every criterion in architecture §11.5 plus written human sign-off recorded in
#: the audit trail. Reaching this phase number is necessary, never sufficient.
LIVE_UNLOCK_PHASE: Final[int] = 4


def live_trading_unlocked() -> bool:
    """Return whether the phase constant permits constructing the LIVE profile.

    Note the asymmetry: a ``True`` here does not authorise trading. It only means this one
    lock layer is open. Layer 1 (no credential exists) still applies.
    """
    return PHASE >= LIVE_UNLOCK_PHASE
