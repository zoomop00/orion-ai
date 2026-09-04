"""The series must be the shipped calculation, not a second one that agrees.

Two tests carry the weight here. `test_the_series_matches_compute_indicators`
is the reuse claim: the last value of this module's series equals
`orion.analysis.indicators.compute_indicators(...).atr` exactly, at several
window lengths. `test_the_converged_warmup_constant_is_earned` re-measures the
convergence that justifies `CONVERGED_WARMUP_BARS = 200` on every run, so the
constant cannot outlive the behaviour it was derived from.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pandas_ta_classic as ta
import pytest

from orion.analysis.indicators import bars_to_dataframe, compute_indicators
from orion.calibration.atr_series import (
    CONVERGED_WARMUP_BARS,
    CONVERGENCE_TOLERANCE,
    CalibrationError,
    atr_percent_series,
)
from orion.data.models import Timeframe
from orion.data.sessions import XAUUSD_WEEKLY
from tests.calibration.conftest import (
    BASE_TIME,
    TIMEFRAME,
    make_walk_bars,
    make_weekend_bars,
)

ATR_LENGTH = 14


def _series(bars: list, *, warmup_bars: int = 20, timeframe: Timeframe = TIMEFRAME) -> tuple:
    return atr_percent_series(
        bars,
        timeframe=timeframe,
        session=XAUUSD_WEEKLY,
        atr_length=ATR_LENGTH,
        warmup_bars=warmup_bars,
    )


# --- the reuse claim ---


@pytest.mark.parametrize("window", [15, 16, 30, 60, 240])
def test_the_series_matches_compute_indicators(window: int) -> None:
    # The last reading of a series over `window` bars must equal what the live
    # scalar function computes over exactly the same bars. Not "close to" --
    # equal: it is the same call over the same frame.
    bars = make_walk_bars(window)
    shipped = compute_indicators(bars, atr_length=ATR_LENGTH).atr
    assert shipped is not None

    samples = _series(bars, warmup_bars=ATR_LENGTH)
    assert samples[-1].atr_percent == shipped / bars[-1].close
    assert samples[-1].as_of == bars[-1].timestamp


def test_atr_percent_divides_by_the_bar_s_own_close() -> None:
    # The series generalisation of classify_volatility's `atr / last_close`:
    # each reading over the close of the bar it was computed at, never a single
    # trailing close applied to the whole series.
    bars = make_walk_bars(120)
    frame = bars_to_dataframe(bars)
    atr = ta.atr(frame["high"], frame["low"], frame["close"], length=ATR_LENGTH)
    assert atr is not None

    for sample, index in zip(_series(bars, warmup_bars=20), range(20, len(bars)), strict=True):
        expected = float(atr.iloc[index]) / bars[index].close
        assert sample.atr_percent == expected


# --- the constant is earned, not asserted ---


def test_the_converged_warmup_constant_is_earned() -> None:
    # Wilder smoothing is recursive, so a one-pass series and the live
    # pipeline's rolling window agree only once the seed's influence has
    # decayed. This re-derives that agreement rather than trusting the comment
    # on the constant: at CONVERGED_WARMUP_BARS the disagreement must be inside
    # CONVERGENCE_TOLERANCE.
    bars = make_walk_bars(1200)
    frame = bars_to_dataframe(bars)
    full = ta.atr(frame["high"], frame["low"], frame["close"], length=ATR_LENGTH)
    assert full is not None

    worst = 0.0
    for index in range(700, 1200, 41):
        window = compute_indicators(
            bars[index - CONVERGED_WARMUP_BARS + 1 : index + 1], atr_length=ATR_LENGTH
        ).atr
        assert window is not None
        reference = float(full.iloc[index])
        worst = max(worst, abs(window - reference) / reference)

    assert worst <= CONVERGENCE_TOLERANCE, (
        f"a {CONVERGED_WARMUP_BARS}-bar warm-up no longer converges to the live rolling "
        f"window: worst relative difference {worst:.3e} exceeds {CONVERGENCE_TOLERANCE:.0e}"
    )


def test_a_short_warmup_does_not_converge() -> None:
    # The other half of the same claim: 200 is not an arbitrary round number,
    # it is past where a shorter warm-up stops being good enough. If this ever
    # passes at 50 bars, the convergence test above has stopped proving
    # anything.
    bars = make_walk_bars(1200)
    frame = bars_to_dataframe(bars)
    full = ta.atr(frame["high"], frame["low"], frame["close"], length=ATR_LENGTH)
    assert full is not None

    worst = 0.0
    for index in range(700, 1200, 41):
        window = compute_indicators(bars[index - 49 : index + 1], atr_length=ATR_LENGTH).atr
        assert window is not None
        reference = float(full.iloc[index])
        worst = max(worst, abs(window - reference) / reference)

    assert worst > CONVERGENCE_TOLERANCE


# --- warm-up honours ORION's guard, not the library's ---


def test_the_first_sample_is_taken_at_the_warmup_index() -> None:
    bars = make_walk_bars(100)
    samples = _series(bars, warmup_bars=30)
    assert len(samples) == 70
    assert samples[0].as_of == bars[30].timestamp
    assert samples[-1].as_of == bars[-1].timestamp


def test_a_warmup_shorter_than_atr_length_is_refused() -> None:
    # ta.atr yields a value at index 13, but compute_indicators refuses until
    # 15 bars exist. Admitting index 13 would put a reading into the
    # distribution that the live pipeline would never have produced.
    with pytest.raises(CalibrationError, match="must be at least atr_length"):
        _series(make_walk_bars(100), warmup_bars=ATR_LENGTH - 1)


def test_no_sample_is_ever_nan() -> None:
    samples = _series(make_walk_bars(300), warmup_bars=CONVERGED_WARMUP_BARS)
    assert samples
    assert all(not pd.isna(s.atr_percent) for s in samples)
    assert all(s.atr_percent > 0 for s in samples)


# --- session boundary flagging ---


def test_no_sample_crosses_a_boundary_in_a_contiguous_series() -> None:
    samples = _series(make_walk_bars(400), warmup_bars=20)
    assert not any(s.crosses_session_boundary for s in samples)


def test_weekend_bars_flag_exactly_the_windows_that_span_the_closure() -> None:
    # A trailing 14-bar ATR window carries the weekend gap for the first 13
    # bars after each Sunday open, and no others.
    bars = make_weekend_bars(weeks=4)
    samples = _series(bars, warmup_bars=20)

    flagged = [s for s in samples if s.crosses_session_boundary]
    assert flagged, "a weekly closure must flag something"
    # Three closures fall inside the sampled region (the first is inside warm-up),
    # each flagging exactly atr_length windows.
    assert len(flagged) == 3 * ATR_LENGTH


def test_the_flag_needs_the_declared_session_to_mean_anything() -> None:
    # The same bars, read at a timeframe whose interval makes the weekly gap
    # unrecognisable as that closure: nothing is forgiven, so nothing is
    # flagged. The flag reports a declared closure, never merely a long gap.
    bars = make_weekend_bars(weeks=3)
    assert not any(
        s.crosses_session_boundary for s in _series(bars, warmup_bars=20, timeframe=Timeframe.M15)
    )


# --- degenerate inputs fail closed ---


def test_too_few_bars_to_yield_a_sample_is_refused() -> None:
    with pytest.raises(CalibrationError, match="cannot yield a sample"):
        _series(make_walk_bars(30), warmup_bars=30)


def test_a_non_positive_atr_length_is_refused() -> None:
    with pytest.raises(CalibrationError, match="atr_length must be at least 1"):
        atr_percent_series(
            make_walk_bars(100),
            timeframe=TIMEFRAME,
            session=XAUUSD_WEEKLY,
            atr_length=0,
            warmup_bars=20,
        )


def test_the_series_is_deterministic() -> None:
    bars = make_walk_bars(300)
    assert _series(bars, warmup_bars=200) == _series(bars, warmup_bars=200)
    # ...and equal across two independently constructed, value-identical inputs.
    assert _series(make_walk_bars(300), warmup_bars=200) == _series(
        make_walk_bars(300), warmup_bars=200
    )


def test_samples_are_ordered_oldest_first() -> None:
    samples = _series(make_walk_bars(300), warmup_bars=200)
    stamps = [s.as_of for s in samples]
    assert stamps == sorted(stamps)
    assert stamps[0] >= BASE_TIME + 200 * timedelta(hours=1)
