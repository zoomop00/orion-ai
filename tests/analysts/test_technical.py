"""The Technical Analyst: real findings, honest absences, no invention.

WHAT THESE TESTS ARE FOR. A deterministic specialist earns trust by being
checkable, so every assertion here ties a finding back to a number that came
out of `orion.analysis`, or ties an absence back to the concrete thing the data
lacked. The tests that matter most are the negative ones: a concept ORION could
not evaluate must be *listed* as unevaluated, never silently dropped and never
answered with a plausible-looking reading.

THE BARS ARE REAL SERIES, NOT MOCKS. `tests/orchestrator/conftest.py`'s rising
series is reused where the exact shape does not matter, and bars are built by
hand where it does — a window with no swings, a window too short to hold a
fair value gap, two timeframes that lean opposite ways.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.analysis.evidence import build_evidence, evidence_fingerprint
from orion.analysts.knowledge import (
    KNOWLEDGE_VERSION,
    TECHNICAL_KNOWLEDGE,
    StructureBias,
    TechnicalConcept,
)
from orion.analysts.technical import (
    TechnicalAnalystError,
    TimeframeObservation,
    analyze_technical,
)
from orion.analysts.technical_schemas import (
    TECHNICAL_ANALYST_VERSION,
    TechnicalAvailability,
    UnevaluatedReason,
)
from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe

from ..orchestrator.conftest import NOW, make_bars, make_snapshot

SYMBOL = "XAUUSD"
BASE_TIME = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)


def observation(
    bars: list[OHLCVBar] | None = None,
    *,
    timeframe: Timeframe = Timeframe.H1,
    provider: str = "fake",
) -> TimeframeObservation:
    snapshot = make_snapshot(bars=bars if bars is not None else make_bars(), timeframe=timeframe)
    snapshot = snapshot.model_copy(
        update={"source": snapshot.source.model_copy(update={"provider": provider})}
    )
    return TimeframeObservation.from_snapshot(snapshot)


def falling_bars(count: int = 120) -> list[OHLCVBar]:
    """A steadily declining hourly series — the mirror of the shared rising
    one, so a second timeframe can genuinely lean the other way."""
    bars: list[OHLCVBar] = []
    price = 2600.0
    for index in range(count):
        close = price - index
        bars.append(
            OHLCVBar(
                timestamp=BASE_TIME + timedelta(hours=index),
                open=close + 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
            )
        )
    return bars


def flat_bars(count: int) -> list[OHLCVBar]:
    """A series with no range at all: no swings, no gaps, no ATR."""
    return [
        OHLCVBar(
            timestamp=BASE_TIME + timedelta(hours=index),
            open=2500.0,
            high=2500.0,
            low=2500.0,
            close=2500.0,
        )
        for index in range(count)
    ]


def finding_for(report: object, concept: TechnicalConcept) -> object | None:
    return next((item for item in report.findings if item.concept is concept), None)


# ----------------------------------------------------------- real findings


class TestItProducesRealFindings:
    def test_a_rising_series_reports_a_bullish_trend_from_the_real_indicators(self) -> None:
        obs = observation()

        output = analyze_technical([obs], now=NOW)

        assert output.availability is TechnicalAvailability.AVAILABLE
        trend = finding_for(output.timeframes[0], TechnicalConcept.TREND_REGIME)
        assert trend is not None
        assert trend.bias is StructureBias.BULLISH
        # The numbers behind the sentence are the evidence's own, not the
        # analyst's: every one of them is on the IndicatorSnapshot.
        values = {value.name: value.value for value in trend.values}
        assert values["ema_fast"] == obs.evidence.indicators.ema_fast
        assert values["ema_slow"] == obs.evidence.indicators.ema_slow
        assert values["last_close"] == obs.evidence.last_price

    def test_a_falling_series_reports_a_bearish_trend(self) -> None:
        output = analyze_technical([observation(falling_bars())], now=NOW)

        trend = finding_for(output.timeframes[0], TechnicalConcept.TREND_REGIME)
        assert trend is not None
        assert trend.bias is StructureBias.BEARISH

    def test_the_momentum_finding_reads_the_evidences_own_rsi(self) -> None:
        obs = observation()

        output = analyze_technical([obs], now=NOW)

        momentum = finding_for(output.timeframes[0], TechnicalConcept.MOMENTUM_RSI)
        assert momentum is not None
        values = {value.name: value.value for value in momentum.values}
        assert values["rsi"] == obs.evidence.indicators.rsi

    def test_volatility_is_never_directional(self) -> None:
        """A high reading says price is moving, never which way. Giving it a
        side would be the easiest way to smuggle a bias in."""
        for bars in (make_bars(), falling_bars()):
            output = analyze_technical([observation(bars)], now=NOW)
            volatility = finding_for(output.timeframes[0], TechnicalConcept.VOLATILITY_ATR)
            assert volatility is not None
            assert volatility.bias is StructureBias.NEUTRAL

    def test_every_finding_is_marked_deterministic(self) -> None:
        output = analyze_technical([observation()], now=NOW)

        assert output.findings
        assert all(finding.deterministic for finding in output.findings)

    def test_the_same_bars_always_produce_the_same_findings(self) -> None:
        """No clock, no randomness, no model. Byte-identical, every time."""
        bars = make_bars()

        first = analyze_technical([observation(bars)], now=NOW)
        second = analyze_technical([observation(bars)], now=NOW)

        assert first.model_dump_json() == second.model_dump_json()

    def test_the_analyst_and_knowledge_versions_are_stamped(self) -> None:
        output = analyze_technical([observation()], now=NOW)

        assert output.analyst_version == TECHNICAL_ANALYST_VERSION
        assert output.knowledge_version == KNOWLEDGE_VERSION
        assert all(f.knowledge_version == KNOWLEDGE_VERSION for f in output.findings)


# -------------------------------------------------- knowledge vs. computation


class TestKnowledgeAndComputationStaySeparate:
    def test_every_interpretation_is_the_registrys_own_sentence(self) -> None:
        """ORION never writes market commentary: it looks one up."""
        output = analyze_technical([observation()], now=NOW)

        assert output.findings
        for finding in output.findings:
            expected = TECHNICAL_KNOWLEDGE[finding.concept].interpretations[finding.bias]
            assert finding.interpretation == expected

    def test_no_interpretation_carries_a_number_and_every_observation_does(self) -> None:
        """The split, seen from the output side: sentences have no numbers,
        measurements do."""
        output = analyze_technical([observation()], now=NOW)

        for finding in output.findings:
            assert not any(char.isdigit() for char in finding.interpretation)
            assert any(char.isdigit() for char in finding.observation)

    def test_every_finding_carries_the_values_its_observation_rests_on(self) -> None:
        output = analyze_technical([observation()], now=NOW)

        for finding in output.findings:
            assert finding.values, f"{finding.concept.value} has no supporting values"


# ------------------------------------------------------------- provenance


class TestProvenance:
    def test_every_finding_names_the_evidence_it_came_from(self) -> None:
        obs = observation()
        expected = evidence_fingerprint(obs.evidence)

        output = analyze_technical([obs], now=NOW)

        assert output.timeframes[0].evidence_fingerprint == expected
        assert all(f.evidence_fingerprint == expected for f in output.findings)

    def test_provenance_is_copied_from_the_snapshot_not_composed(self) -> None:
        obs = observation(provider="twelvedata")

        output = analyze_technical([obs], now=NOW)

        report = output.timeframes[0]
        assert report.source_provider == "twelvedata"
        assert report.retrieved_at == obs.snapshot.source.retrieved_at
        assert report.as_of == obs.evidence.as_of
        assert report.bar_count == obs.evidence.bar_count
        assert all(f.source_provider == "twelvedata" for f in report.findings)

    def test_observed_at_is_the_callers_now_never_a_clock(self) -> None:
        moment = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)

        output = analyze_technical([observation()], now=moment)

        assert output.analyzed_at == moment
        assert all(finding.observed_at == moment for finding in output.findings)

    def test_a_mismatched_snapshot_and_evidence_pair_is_refused(self) -> None:
        """The pairing cannot be wrong, so a finding's fingerprint cannot
        describe bars other than the ones it was computed from."""
        with pytest.raises(ValueError, match="1h|4h"):
            TimeframeObservation(
                snapshot=make_snapshot(bars=make_bars(), timeframe=Timeframe.H4),
                evidence=build_evidence(make_snapshot(bars=make_bars(), timeframe=Timeframe.H1)),
            )


# -------------------------------------------------------- multi-timeframe


class TestMultipleTimeframes:
    def test_each_timeframe_is_reported_separately(self) -> None:
        output = analyze_technical(
            [
                observation(make_bars(), timeframe=Timeframe.H1),
                observation(make_bars(), timeframe=Timeframe.H4),
            ],
            now=NOW,
        )

        assert [report.timeframe for report in output.timeframes] == [
            Timeframe.H1,
            Timeframe.H4,
        ]
        assert all(report.findings for report in output.timeframes)

    def test_findings_never_leak_across_timeframes(self) -> None:
        output = analyze_technical(
            [
                observation(make_bars(), timeframe=Timeframe.H1),
                observation(falling_bars(), timeframe=Timeframe.H4),
            ],
            now=NOW,
        )

        for report in output.timeframes:
            assert all(finding.timeframe is report.timeframe for finding in report.findings)
            assert all(
                finding.evidence_fingerprint == report.evidence_fingerprint
                for finding in report.findings
            )

    def test_agreeing_timeframes_are_reported_as_agreeing(self) -> None:
        output = analyze_technical(
            [
                observation(make_bars(), timeframe=Timeframe.H1),
                observation(make_bars(), timeframe=Timeframe.H4),
            ],
            now=NOW,
        )

        assert output.timeframe_agreement is True
        assert output.bias is StructureBias.BULLISH

    def test_disagreeing_timeframes_are_reported_as_disagreeing(self) -> None:
        """NOT AVERAGED AWAY. Two timeframes leaning opposite ways is a real
        finding a reader needs, and an aggregate that hid it would be worse
        than useless."""
        output = analyze_technical(
            [
                observation(make_bars(), timeframe=Timeframe.H1),
                observation(falling_bars(), timeframe=Timeframe.H4),
            ],
            now=NOW,
        )

        assert output.timeframe_agreement is False
        assert {report.bias for report in output.timeframes} == {
            StructureBias.BULLISH,
            StructureBias.BEARISH,
        }
        # A one-all tie is NEUTRAL, not a coin toss and not a weighting nobody
        # calibrated.
        assert output.bias is StructureBias.NEUTRAL

    def test_a_repeated_timeframe_is_refused(self) -> None:
        with pytest.raises(TechnicalAnalystError, match="once"):
            analyze_technical(
                [
                    observation(make_bars(), timeframe=Timeframe.H1),
                    observation(make_bars(), timeframe=Timeframe.H1),
                ],
                now=NOW,
            )

    def test_a_pass_covers_one_symbol(self) -> None:
        other = make_snapshot(bars=make_bars())
        other = other.model_copy(
            update={"source": other.source.model_copy(update={"symbol": "EURUSD"})}
        )

        with pytest.raises(TechnicalAnalystError, match="one symbol"):
            analyze_technical([observation(), TimeframeObservation.from_snapshot(other)], now=NOW)


# --------------------------------------------- absence is reported, not filled


class TestAbsenceIsReported:
    def test_no_input_is_an_explicit_state_not_an_empty_success(self) -> None:
        output = analyze_technical([], now=NOW)

        assert output.availability is TechnicalAvailability.NO_INPUT
        assert output.timeframes == ()
        assert output.findings == ()
        assert output.bias is StructureBias.UNCLEAR
        assert output.timeframe_agreement is False

    def test_a_window_too_thin_for_any_concept_reports_insufficient_data(self) -> None:
        output = analyze_technical([observation(make_bars(count=2))], now=NOW)

        assert output.availability is TechnicalAvailability.INSUFFICIENT_DATA
        assert output.findings == ()
        # And every concept is still accounted for, with a reason each.
        assert {item.concept for item in output.timeframes[0].unevaluated} == set(TechnicalConcept)

    def test_every_concept_appears_exactly_once_per_timeframe(self) -> None:
        """The completeness invariant: a concept cannot silently vanish."""
        for bars in (make_bars(), make_bars(count=2), flat_bars(30)):
            report = analyze_technical([observation(bars)], now=NOW).timeframes[0]
            seen = [f.concept for f in report.findings] + [u.concept for u in report.unevaluated]
            assert sorted(c.value for c in seen) == sorted(c.value for c in TechnicalConcept)

    def test_a_window_with_no_swings_says_so_rather_than_inventing_levels(self) -> None:
        """The shared rising series never puts in a fractal turn, so ORION has
        no confirmed swing to measure structure against — and says exactly
        that instead of using the window's extremes."""
        report = analyze_technical([observation()], now=NOW).timeframes[0]

        reasons = {item.concept: item.reason for item in report.unevaluated}
        for concept in (
            TechnicalConcept.SWING_STRUCTURE,
            TechnicalConcept.SUPPORT_RESISTANCE,
            TechnicalConcept.BREAK_OF_STRUCTURE,
            TechnicalConcept.CHANGE_OF_CHARACTER,
            TechnicalConcept.LIQUIDITY_SWEEP,
        ):
            assert reasons[concept] is UnevaluatedReason.NO_CONFIRMED_SWINGS

    def test_fibonacci_is_unevaluated_without_a_valid_anchor(self) -> None:
        report = analyze_technical([observation()], now=NOW).timeframes[0]

        reasons = {item.concept: item.reason for item in report.unevaluated}
        assert reasons[TechnicalConcept.FIBONACCI_RETRACEMENT] is UnevaluatedReason.NO_VALID_ANCHOR
        assert finding_for(report, TechnicalConcept.FIBONACCI_RETRACEMENT) is None

    def test_a_two_bar_window_cannot_evaluate_fair_value_gaps(self) -> None:
        report = analyze_technical([observation(make_bars(count=2))], now=NOW).timeframes[0]

        reasons = {item.concept: item.reason for item in report.unevaluated}
        assert reasons[TechnicalConcept.FAIR_VALUE_GAP] is UnevaluatedReason.INSUFFICIENT_BARS

    def test_missing_indicators_are_reported_as_indicator_unavailable(self) -> None:
        report = analyze_technical([observation(make_bars(count=10))], now=NOW).timeframes[0]

        reasons = {item.concept: item.reason for item in report.unevaluated}
        for concept in (
            TechnicalConcept.TREND_REGIME,
            TechnicalConcept.MOVING_AVERAGE_ALIGNMENT,
        ):
            assert reasons[concept] is UnevaluatedReason.INDICATOR_UNAVAILABLE

    def test_looked_and_found_nothing_is_a_finding_not_an_absence(self) -> None:
        """THE DISTINCTION THIS DESIGN RESTS ON. A flat window has confirmed
        swings but no structural events, so BOS/CHOCH/sweeps are evaluated and
        report a count of zero — a measured absence, reported as a NEUTRAL
        finding rather than as "could not look".
        """
        report = analyze_technical([observation(flat_bars(40))], now=NOW).timeframes[0]

        for concept in (
            TechnicalConcept.BREAK_OF_STRUCTURE,
            TechnicalConcept.CHANGE_OF_CHARACTER,
            TechnicalConcept.LIQUIDITY_SWEEP,
        ):
            found = finding_for(report, concept)
            assert found is not None, f"{concept.value} should have been evaluated"
            assert found.bias is StructureBias.NEUTRAL
            assert "0" in found.observation


# ------------------------------------------------------------- refusals


def test_a_naive_now_is_refused() -> None:
    with pytest.raises(TechnicalAnalystError, match="timezone-aware"):
        analyze_technical([observation()], now=datetime(2026, 8, 30, 12, 0))


def test_a_pass_over_no_input_still_refuses_a_naive_now() -> None:
    """Checked before the empty-input shortcut, so a bad call never returns a
    result that looks valid."""
    with pytest.raises(TechnicalAnalystError, match="timezone-aware"):
        analyze_technical([], now=datetime(2026, 8, 30, 12, 0))


def test_the_analyst_needs_no_credential_and_contacts_nobody() -> None:
    """A deterministic specialist has no vendor. Proven by behaviour: this runs
    with the environment stripped of every credential the build knows about."""
    import os

    saved = {
        key: os.environ.pop(key, None)
        for key in ("ANTHROPIC_API_KEY", "TWELVE_DATA_API_KEY", "FINNHUB_API_KEY")
    }
    try:
        output = analyze_technical([observation()], now=NOW)
        assert output.availability is TechnicalAvailability.AVAILABLE
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_a_snapshot_meta_that_disagrees_with_its_evidence_is_refused() -> None:
    source = DataSourceMeta(
        provider="fake", symbol="XAUUSD", timeframe=Timeframe.H1, retrieved_at=NOW
    )
    snapshot = MarketSnapshot(source=source, bars=make_bars())
    evidence = build_evidence(snapshot)

    with pytest.raises(ValueError, match="bars"):
        TimeframeObservation(
            snapshot=MarketSnapshot(source=source, bars=make_bars(count=10)),
            evidence=evidence,
        )
