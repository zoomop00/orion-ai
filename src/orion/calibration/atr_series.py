"""The ATR-as-percent-of-price series, one sample per bar.

WHY A SERIES EXISTS AT ALL. `orion.analysis.indicators.compute_indicators`
answers "what is ATR right now" — one scalar off the end of a window, which is
all a decision cycle needs. A distribution needs every reading, and nothing in
ORION produced one. This module is that, and only that: it measures, and it
never classifies. `orion.analysis.structure.classify_volatility` remains the
only place a number becomes LOW/MEDIUM/HIGH, and its thresholds are untouched
by this package.

IT REUSES THE SHIPPED CALCULATION RATHER THAN RESTATING IT. `ta.atr`'s
`mamode` defaults to `"rma"` — Wilder's smoothing — with `drift=1`, and
`compute_indicators` passes neither, so those defaults *are* ORION's ATR. The
same call is made here over the whole frame instead of one window, and
`tests/calibration/test_atr_series.py` proves the last value of this series
equals `compute_indicators(bars).atr` exactly, at several window lengths. That
test is the reuse claim; without it this would merely be a second
implementation that happens to agree today.

WARM-UP IS ORION'S, NOT THE LIBRARY'S. `ta.atr` yields its first value at index
`atr_length - 1` (the 14th bar at length 14), but `compute_indicators` returns
`None` until `atr_length + 1` bars exist. Honouring the library's looser bound
would put a reading into the distribution that the live pipeline would have
refused to compute, so `warmup_bars` is required to be at least `atr_length`
and the first sample is taken at index `warmup_bars`.

WILDER'S ATR IS PATH-DEPENDENT, WHICH IS WHY `warmup_bars` MATTERS MORE THAN A
NaN COUNT. RMA is recursive: the reading at bar `i` depends on every bar
supplied, not just the trailing `atr_length`. So a one-pass series over a long
dataset and the live pipeline's rolling window do not automatically agree — the
seed's influence decays but does not vanish. `CONVERGED_WARMUP_BARS` below is
the measured point at which it stops mattering, and
`tests/calibration/test_atr_series.py::test_the_converged_warmup_constant_is_earned`
re-measures it on every run rather than trusting this paragraph.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import pandas as pd
import pandas_ta_classic as ta
from pydantic import BaseModel, ConfigDict

from orion.analysis.indicators import bars_to_dataframe
from orion.data.models import OHLCVBar, Timeframe
from orion.data.sessions import TradingSession
from orion.data.validation import TIMEFRAME_INTERVAL

#: Leading bars to discard so a one-pass series matches what the live pipeline
#: would have computed over its own rolling window.
#:
#: Measured, not chosen for roundness. Comparing this module's series against
#: `compute_indicators` over rolling windows of the same width gives a worst
#: relative disagreement of roughly 9.4e-03 at 50 bars, 2.5e-04 at 100, and
#: 1.0e-07 at 200 — the seed's influence decaying as `(1 - 1/atr_length)**k`.
#: 200 is also exactly `orion.orchestrator.analysis.DEFAULT_OUTPUT_SIZE`, the
#: window live analysis actually fetches, so a sample taken here is a sample the
#: live pipeline could have produced. A test re-derives this on every run and
#: fails if the agreement degrades past `CONVERGENCE_TOLERANCE`.
CONVERGED_WARMUP_BARS = 200

#: The bound `CONVERGED_WARMUP_BARS` is required to meet. Three orders of
#: magnitude tighter than the measured 1.0e-07, and far tighter than the three
#: significant figures any volatility threshold could carry.
CONVERGENCE_TOLERANCE = 1e-6


class CalibrationError(RuntimeError):
    """Raised when a measurement cannot be computed from the given input.

    Own top-level hierarchy, matching this codebase's "every layer owns its own
    RuntimeError" rule. Never caught and papered over with a `NaN`, an `inf` or
    a fabricated `0.0` — a distribution built from a silently-skipped sample
    would misreport the very thing it exists to measure.
    """


class AtrPercentSample(BaseModel):
    """One bar's ATR-as-percent-of-its-own-close reading.

    `crosses_session_boundary` records whether this sample's trailing
    `atr_length` true ranges include a bar that opened after a declared market
    closure. Such a true range carries the weekend gap, and Wilder smoothing
    spreads it across the following readings — so the flag is not a defect
    marker, it is the fact a caller needs in order to know how gap-laden a
    distribution is before drawing a threshold from it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    atr_percent: float
    crosses_session_boundary: bool


def atr_percent_series(
    bars: Sequence[OHLCVBar],
    *,
    timeframe: Timeframe,
    session: TradingSession,
    atr_length: int,
    warmup_bars: int,
) -> tuple[AtrPercentSample, ...]:
    """Every usable ATR% reading over `bars`, oldest first.

    `atr_percent` is `ATR(atr_length)[i] / bars[i].close` — the series
    generalisation of `orion.analysis.structure.classify_volatility`'s
    `indicators.atr / last_close`, dividing each reading by the close of the
    bar it was computed at, which is the only form that agrees with the scalar
    at every point.

    Every parameter is required. `atr_length` and `warmup_bars` are not
    defaulted here even though `compute_indicators` defaults the former: a
    measurement that quietly assumed a parameter would be uncheckable against
    the study that reports it.

    Raises `CalibrationError` if `warmup_bars < atr_length` (which would admit a
    reading the live pipeline refuses to compute), if the arguments are
    otherwise degenerate, if `bars` is too short to yield a single sample, or if
    the underlying calculation produces a non-finite value where a sample was
    expected — never a silently shortened series.

    Pure: no clock, no network, no filesystem, no randomness.
    """
    if atr_length < 1:
        raise CalibrationError(f"atr_length must be at least 1, got {atr_length}")
    if warmup_bars < atr_length:
        raise CalibrationError(
            f"warmup_bars ({warmup_bars}) must be at least atr_length ({atr_length}); a "
            "shorter warm-up would admit a reading orion.analysis.indicators."
            "compute_indicators itself refuses to compute"
        )
    if len(bars) <= warmup_bars:
        raise CalibrationError(
            f"{len(bars)} bars cannot yield a sample after discarding {warmup_bars} to "
            "warm-up; there is nothing to measure"
        )

    frame = bars_to_dataframe(list(bars))
    atr = ta.atr(frame["high"], frame["low"], frame["close"], length=atr_length)
    if atr is None:
        raise CalibrationError(
            f"ATR({atr_length}) could not be computed over {len(bars)} bars; the indicator "
            "returned nothing rather than a series"
        )

    boundaries = _session_boundary_flags(bars, timeframe=timeframe, session=session)

    samples: list[AtrPercentSample] = []
    for index in range(warmup_bars, len(bars)):
        value = atr.iloc[index]
        if pd.isna(value):
            raise CalibrationError(
                f"ATR({atr_length}) is undefined at bar {index} "
                f"({bars[index].timestamp.isoformat()}) despite {warmup_bars} warm-up bars"
            )
        samples.append(
            AtrPercentSample(
                as_of=bars[index].timestamp,
                atr_percent=float(value) / bars[index].close,
                crosses_session_boundary=any(
                    boundaries[max(1, index - atr_length + 1) : index + 1]
                ),
            )
        )
    return tuple(samples)


def _session_boundary_flags(
    bars: Sequence[OHLCVBar], *, timeframe: Timeframe, session: TradingSession
) -> list[bool]:
    """For each index `j`, whether the true range at `j` spans a declared
    closure.

    A true range at `j` is computed from `bars[j]` and `bars[j - 1]`, so the
    pair that matters is that adjacency — not the bar alone. Index 0 has no
    predecessor and is `False` by construction; it is also always inside the
    warm-up, so it never reaches a sample.
    """
    interval: timedelta = TIMEFRAME_INTERVAL[timeframe]
    flags = [False]
    for index in range(1, len(bars)):
        flags.append(
            session.is_expected_closure(bars[index - 1].timestamp, bars[index].timestamp, interval)
        )
    return flags
