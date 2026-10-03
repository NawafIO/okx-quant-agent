"""The trial counter - G-8's denominator (architecture §11.3, finding R-1).

Every configuration ever evaluated is appended here: selection candidates, perturbations,
stress runs, final runs. The log sits on the project's hash-chained audit trail, so a trial
cannot be removed, edited or reordered without :func:`verify_chain` failing. Deleting the
whole file restarts it at genesis, which no chain can detect by itself - so every gate
report records the chain head and count it was evaluated against (finding A-11/A-12 note),
and a later count lower than a recorded one is evidence of deletion.

The log lives outside :class:`BacktestResult`: its records carry wall-clock timestamps,
which must never enter a result that is compared bit-for-bit (finding A-6).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from okxq.audit.chain import GENESIS_HASH, AuditChain, read_chain, verify_chain
from okxq.backtest.gates import TrialStats

TRIAL_KIND = "trial"


def params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Trial:
    strategy_id: str
    strategy_version: str
    params: dict[str, Any]
    window_start_ms: int
    window_end_ms: int
    #: selection | perturbation | stress | final | holdout
    purpose: str
    n_trades: int
    #: Per-bar (not annualised) Sharpe of the MTM curve; None if undefined.
    per_period_sharpe: float | None
    profit_factor: str | None
    result_digest: str


class TrialLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        if path.exists():
            verify_chain(path)
        self._chain = AuditChain(path)

    def record(self, trial: Trial) -> None:
        payload = {
            "strategy_id": trial.strategy_id,
            "strategy_version": trial.strategy_version,
            "params": trial.params,
            "params_hash": params_hash(trial.params),
            "window": [trial.window_start_ms, trial.window_end_ms],
            "purpose": trial.purpose,
            "n_trades": trial.n_trades,
            "per_period_sharpe": trial.per_period_sharpe,
            "profit_factor": trial.profit_factor,
            "result_digest": trial.result_digest,
        }
        self._chain.append(TRIAL_KIND, payload)

    def stats(self) -> TrialStats:
        """N = every trial ever recorded; V = sample variance of the defined Sharpes."""
        if not self._path.exists():
            return TrialStats(0, 0.0, GENESIS_HASH)
        verify_chain(self._path)
        records = [r for r in read_chain(self._path) if r.kind == TRIAL_KIND]
        head = read_chain(self._path)[-1].hash if records else GENESIS_HASH
        srs = [
            float(r.payload["per_period_sharpe"])
            for r in records
            if r.payload.get("per_period_sharpe") is not None
        ]
        if len(srs) >= 2:
            mu = math.fsum(srs) / len(srs)
            var = math.fsum((x - mu) ** 2 for x in srs) / (len(srs) - 1)
        else:
            var = 0.0
        return TrialStats(n_trials=len(records), sharpe_variance=var, chain_head=head)
