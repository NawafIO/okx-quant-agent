"""The right-truncated history a strategy sees (architecture §11.1).

Look-ahead is prevented by **where the data is**, not by a cursor a careful developer would
respect. The engine owns the full series and copies a bar into a :class:`HistoryBuffer` only
when that bar has CLOSED. Nothing reachable from a :class:`HistoryView` - not its attributes,
not a numpy ``.base``, not the buffer behind it - holds a bar that has not closed yet, because
that bar has not been written anywhere the view can reach. Unfilled slots are NaN, not data.

Every accessor returns a copy, so a strategy cannot write into the buffer and corrupt what a
later decision (or a later strategy) reads.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

_FIELDS = ("open", "high", "low", "close", "volume")


class HistoryBuffer:
    """Append-only per-instrument bar store, written only by the engine on bar close.

    Prices are float64 here: this is the analysis view for indicators. Fills and accounting
    use the engine's exact ``Decimal`` series, which no strategy can reach.
    """

    __slots__ = ("_cols", "_n", "_ts", "inst_id")

    def __init__(self, inst_id: str, capacity: int) -> None:
        self.inst_id = inst_id
        self._ts = np.zeros(capacity, dtype=np.int64)
        self._cols = {name: np.full(capacity, np.nan) for name in _FIELDS}
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def append(self, ts_open_ms: int, o: float, h: float, low: float, c: float, v: float) -> None:
        i = self._n
        self._ts[i] = ts_open_ms
        for name, value in zip(_FIELDS, (o, h, low, c, v), strict=True):
            self._cols[name][i] = value
        self._n = i + 1

    def view(self) -> HistoryView:
        return HistoryView(self, self._n)


class HistoryView:
    """Immutable window ``[0, n)`` over one instrument's closed bars.

    ``n`` is fixed at construction: a view kept past its callback never grows, so retaining
    one cannot become a side door to later bars either.
    """

    __slots__ = ("_buf", "_n")

    def __init__(self, buf: HistoryBuffer, n: int) -> None:
        self._buf = buf
        self._n = n

    def __len__(self) -> int:
        return self._n

    @property
    def inst_id(self) -> str:
        return self._buf.inst_id

    def _span(self, lookback: int | None) -> slice:
        if lookback is None:
            return slice(0, self._n)
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        return slice(max(0, self._n - lookback), self._n)

    def _col(self, name: str, lookback: int | None) -> FloatArray:
        out: FloatArray = self._buf._cols[name][self._span(lookback)].copy()
        return out

    def ts_open_ms(self, lookback: int | None = None) -> npt.NDArray[np.int64]:
        out: npt.NDArray[np.int64] = self._buf._ts[self._span(lookback)].copy()
        return out

    def opens(self, lookback: int | None = None) -> FloatArray:
        return self._col("open", lookback)

    def highs(self, lookback: int | None = None) -> FloatArray:
        return self._col("high", lookback)

    def lows(self, lookback: int | None = None) -> FloatArray:
        return self._col("low", lookback)

    def closes(self, lookback: int | None = None) -> FloatArray:
        return self._col("close", lookback)

    def volumes(self, lookback: int | None = None) -> FloatArray:
        return self._col("volume", lookback)

    def bar(self, i: int) -> tuple[int, float, float, float, float, float]:
        """Bar ``i`` (negative counts from the latest closed bar).

        Raises:
            IndexError: for any index at or beyond the current bar - the next bar does not
                exist yet from the strategy's point of view, so asking for it is an error.
        """
        if i < 0:
            i += self._n
        if not 0 <= i < self._n:
            raise IndexError(f"bar {i} is not in the closed history (len {self._n})")
        cols = self._buf._cols
        return (
            int(self._buf._ts[i]),
            float(cols["open"][i]),
            float(cols["high"][i]),
            float(cols["low"][i]),
            float(cols["close"][i]),
            float(cols["volume"][i]),
        )

    def __getitem__(self, i: int) -> tuple[int, float, float, float, float, float]:
        return self.bar(i)

    @property
    def last_close(self) -> float:
        return self.bar(-1)[4]
