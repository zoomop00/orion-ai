from __future__ import annotations

from datetime import UTC, datetime

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.analysis.indicators import IndicatorSnapshot
from orion.analysis.structure import StructureSnapshot, TrendDirection, VolatilityLevel
from orion.data.models import Timeframe

BASE_TIME = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _evidence(*, last_price: float = 2400.0, bar_count: int = 60) -> Evidence:
    return Evidence(
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        as_of=BASE_TIME,
        last_price=last_price,
        bar_count=bar_count,
        indicators=IndicatorSnapshot(
            ema_fast=2405.0,
            ema_slow=2390.0,
            sma_slow=2380.0,
            rsi=62.0,
            atr=8.5,
            ema_fast_length=20,
            ema_slow_length=50,
            sma_length=50,
            rsi_length=14,
            atr_length=14,
        ),
        structure=StructureSnapshot(
            trend=TrendDirection.BULLISH,
            volatility=VolatilityLevel.MEDIUM,
            nearest_support=2385.0,
            nearest_resistance=2415.0,
            swing_high_count=3,
            swing_low_count=2,
        ),
    )


def test_same_evidence_object_yields_the_same_fingerprint() -> None:
    evidence = _evidence()
    assert evidence_fingerprint(evidence) == evidence_fingerprint(evidence)


def test_equivalent_but_separately_constructed_evidence_yields_the_same_fingerprint() -> None:
    # Two independently-built Evidence instances with identical field values —
    # not the same object, not even the same nested-model instances.
    assert evidence_fingerprint(_evidence()) == evidence_fingerprint(_evidence())


def test_modified_evidence_yields_a_different_fingerprint() -> None:
    baseline = evidence_fingerprint(_evidence())
    assert evidence_fingerprint(_evidence(last_price=2400.01)) != baseline
    assert evidence_fingerprint(_evidence(bar_count=61)) != baseline


def test_fingerprint_is_a_64_character_hex_string() -> None:
    fingerprint = evidence_fingerprint(_evidence())
    assert len(fingerprint) == 64
    assert all(c in "0123456789abcdef" for c in fingerprint)
