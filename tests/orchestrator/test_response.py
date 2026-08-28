"""The `ResponseProvider` that turns "analyze gold" into speech.

Three things are load-bearing here and each has its own section: the
fallback for anything ORION does not understand still goes through Mission
9's own static provider; every failure becomes a `ResponseProviderError`
with a fixed message rather than a spoken guess; and `last_report` can never
hand back a stale analysis.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orion.data.provider_base import MarketDataProviderError
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.response import (
    ANALYSIS_FAILED_MESSAGE,
    MAX_SPOKEN_CHARS,
    AnalysisResponseProvider,
    summarize,
)
from orion.orchestrator.schemas import RiskAvailability
from orion.reasoning.provider_base import ReasoningProviderError
from orion.reasoning.schemas import Action, Direction
from orion.risk.policy import RiskPolicy
from orion.voice.response import DEFAULT_RESPONSE_TEXT, ResponseProvider, ResponseProviderError
from orion.voice.schemas import Transcript

from .conftest import (
    LAST_BAR_TIME,
    NOW,
    FakeMarketDataProvider,
    FakePortfolioStore,
    FakeReasoningProvider,
    make_portfolio,
    make_snapshot,
    make_transcript,
)


def make_provider(
    *,
    policy: RiskPolicy,
    market_data: FakeMarketDataProvider | None = None,
    reasoning_provider: FakeReasoningProvider | None = None,
    portfolio_store: FakePortfolioStore | None = None,
    fallback: ResponseProvider | None = None,
) -> AnalysisResponseProvider:
    service = AnalysisService(
        market_data=market_data or FakeMarketDataProvider(),
        reasoning_provider=reasoning_provider or FakeReasoningProvider(),
        policy=policy,
        portfolio_store=portfolio_store,
    )
    return AnalysisResponseProvider(service, fallback=fallback)


class TestItIsAResponseProvider:
    def test_satisfies_the_mission_9_contract(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(policy=permissive_policy)
        assert isinstance(provider, ResponseProvider)

    def test_answers_an_analyse_request_with_a_real_analysis(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(
            policy=permissive_policy, portfolio_store=FakePortfolioStore(make_portfolio())
        )

        spoken = provider.respond(make_transcript("Orion, analyze gold"))

        assert provider.last_report is not None
        assert provider.last_report.symbol == "XAUUSD"
        assert spoken == summarize(provider.last_report)

    def test_uses_the_transcripts_own_timestamp_as_the_clock(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(policy=permissive_policy)
        asked_at = NOW + timedelta(minutes=30)

        provider.respond(make_transcript("analyze gold", transcribed_at=asked_at))

        assert provider.last_report is not None
        assert provider.last_report.analyzed_at == asked_at


class TestTheFallback:
    def test_anything_it_does_not_understand_gets_the_mission_9_answer(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(policy=permissive_policy)

        for text in ("hello orion", "what is the weather", "buy gold now", "analyze bitcoin"):
            assert provider.respond(make_transcript(text)) == DEFAULT_RESPONSE_TEXT

    def test_an_unrecognised_request_runs_no_analysis_at_all(
        self, permissive_policy: RiskPolicy
    ) -> None:
        market_data = FakeMarketDataProvider()
        store = FakePortfolioStore(make_portfolio())
        provider = make_provider(
            policy=permissive_policy, market_data=market_data, portfolio_store=store
        )

        provider.respond(make_transcript("hello orion"))

        assert market_data.calls == []
        assert store.load_calls == 0
        assert provider.last_report is None

    def test_the_fallback_is_injectable(self, permissive_policy: RiskPolicy) -> None:
        class Custom(ResponseProvider):
            def respond(self, transcript: Transcript) -> str:
                return "custom answer"

        provider = make_provider(policy=permissive_policy, fallback=Custom())
        assert provider.respond(make_transcript("hello")) == "custom answer"


class TestFailureIsNeverAVerdict:
    @pytest.mark.parametrize(
        "error",
        [
            MarketDataProviderError("twelvedata returned 401 for key sk-live-abc"),
            ReasoningProviderError("anthropic refused: see https://vendor.example/errors/42"),
        ],
    )
    def test_a_provider_failure_becomes_a_fixed_message(
        self, permissive_policy: RiskPolicy, error: Exception
    ) -> None:
        if isinstance(error, MarketDataProviderError):
            provider = make_provider(
                policy=permissive_policy, market_data=FakeMarketDataProvider(error=error)
            )
        else:
            provider = make_provider(
                policy=permissive_policy,
                reasoning_provider=FakeReasoningProvider(error=error),
            )

        with pytest.raises(ResponseProviderError) as caught:
            provider.respond(make_transcript("analyze gold"))

        message = str(caught.value)
        assert message == ANALYSIS_FAILED_MESSAGE
        # No vendor name, no credential, no URL, no status code.
        assert "sk-live" not in message
        assert "anthropic" not in message.lower()
        assert "twelvedata" not in message.lower()
        assert "http" not in message.lower()
        assert "401" not in message

    def test_the_cause_is_still_chained_for_a_debugger(self, permissive_policy: RiskPolicy) -> None:
        original = MarketDataProviderError("upstream is down")
        provider = make_provider(
            policy=permissive_policy, market_data=FakeMarketDataProvider(error=original)
        )

        with pytest.raises(ResponseProviderError) as caught:
            provider.respond(make_transcript("analyze gold"))

        assert caught.value.__cause__ is original

    def test_unusable_market_data_never_becomes_a_spoken_guess(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(
            policy=permissive_policy, market_data=FakeMarketDataProvider(make_snapshot(bars=[]))
        )

        with pytest.raises(ResponseProviderError):
            provider.respond(make_transcript("analyze gold"))
        assert provider.last_report is None

    def test_a_failure_leaves_no_report_behind(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(policy=permissive_policy)

        provider.respond(make_transcript("analyze gold"))
        assert provider.last_report is not None

        provider._service = AnalysisService(  # deliberate reach-in, see below
            market_data=FakeMarketDataProvider(error=MarketDataProviderError("down")),
            reasoning_provider=FakeReasoningProvider(),
            policy=permissive_policy,
        )
        with pytest.raises(ResponseProviderError):
            provider.respond(make_transcript("analyze gold"))

        # The previous turn's analysis must not be readable as if it were
        # this turn's. Reaching into the private attribute is the only way
        # to simulate "the same instance, a different outcome" without
        # inventing a second seam in production code purely for a test.
        assert provider.last_report is None


class TestLastReportIsNeverStale:
    def test_is_cleared_when_the_next_request_is_not_understood(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(policy=permissive_policy)

        provider.respond(make_transcript("analyze gold"))
        assert provider.last_report is not None

        provider.respond(make_transcript("hello orion"))
        assert provider.last_report is None

    def test_is_replaced_by_each_new_analysis(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(policy=permissive_policy)

        provider.respond(make_transcript("analyze gold", transcribed_at=NOW))
        first = provider.last_report

        provider.respond(
            make_transcript("analyze gold", transcribed_at=NOW + timedelta(minutes=10))
        )
        second = provider.last_report

        assert first is not None and second is not None
        assert second is not first
        assert second.analyzed_at != first.analyzed_at


class TestTheSpokenSummary:
    def test_is_deterministic(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(policy=permissive_policy)
        first = provider.respond(make_transcript("analyze gold"))
        second = provider.respond(make_transcript("analyze gold"))
        assert first == second

    def test_names_the_market_the_way_a_person_would(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(policy=permissive_policy)
        spoken = provider.respond(make_transcript("analyze gold"))
        assert spoken.startswith("Gold on the hourly chart")

    def test_reports_what_the_engines_actually_found(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(
            policy=permissive_policy, portfolio_store=FakePortfolioStore(make_portfolio())
        )
        spoken = provider.respond(make_transcript("analyze gold"))
        report = provider.last_report
        assert report is not None

        assert "trend is bullish" in spoken
        assert f"{report.evidence.indicators.rsi:.1f}" in spoken
        assert "my call is to buy" in spoken
        assert "55 percent confidence" in spoken
        assert "risk guardian approved" in spoken.lower()

    def test_says_out_loud_when_there_is_no_risk_review(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(policy=permissive_policy, portfolio_store=None)
        spoken = provider.respond(make_transcript("analyze gold"))

        assert provider.last_report is not None
        assert provider.last_report.risk.availability is RiskAvailability.NO_PORTFOLIO_STORE
        assert "could not run a risk review" in spoken
        assert "no paper account is connected" in spoken

    def test_says_out_loud_when_momentum_has_no_reading(
        self, permissive_policy: RiskPolicy
    ) -> None:
        # A short history leaves `IndicatorSnapshot.rsi` as None. That must
        # be spoken as an absence, never smoothed over with a number.
        from .conftest import make_bars

        short = make_snapshot(bars=make_bars(count=3, end=LAST_BAR_TIME))
        provider = make_provider(
            policy=permissive_policy, market_data=FakeMarketDataProvider(short)
        )
        spoken = provider.respond(make_transcript("analyze gold"))

        assert provider.last_report is not None
        assert provider.last_report.evidence.indicators.rsi is None
        assert "not enough history yet for a momentum reading" in spoken
        assert "R S I, is at" not in spoken

    def test_covers_every_action_and_direction_pair_it_can_be_handed(
        self, permissive_policy: RiskPolicy
    ) -> None:
        cases = [
            (Action.BUY, Direction.LONG, 2489.0, 2600.0),
            (Action.SELL, Direction.SHORT, 2600.0, 2400.0),
            (Action.WAIT, Direction.LONG, None, None),
            (Action.WAIT, Direction.SHORT, None, None),
            (Action.WAIT, Direction.NONE, None, None),
        ]
        for action, direction, stop, target in cases:
            provider = make_provider(
                policy=permissive_policy,
                reasoning_provider=FakeReasoningProvider(
                    action=action, direction=direction, stop_loss=stop, take_profit=target
                ),
            )
            spoken = provider.respond(make_transcript("analyze gold"))
            assert "my call is to" in spoken, (action, direction)

    def test_covers_every_risk_availability_reason(self, permissive_policy: RiskPolicy) -> None:
        from orion.paper.store import PortfolioStoreError

        stores = {
            RiskAvailability.NO_PORTFOLIO_STORE: None,
            RiskAvailability.PORTFOLIO_UNAVAILABLE: FakePortfolioStore(
                error=PortfolioStoreError("x")
            ),
            RiskAvailability.PORTFOLIO_SYMBOL_MISMATCH: FakePortfolioStore(
                make_portfolio(symbol="EURUSD")
            ),
            RiskAvailability.AVAILABLE: FakePortfolioStore(make_portfolio()),
        }
        for expected, store in stores.items():
            provider = make_provider(policy=permissive_policy, portfolio_store=store)
            spoken = provider.respond(make_transcript("analyze gold"))
            assert provider.last_report is not None
            assert provider.last_report.risk.availability is expected
            assert spoken.strip()

    def test_stays_inside_what_a_speech_synthesiser_will_accept(
        self, permissive_policy: RiskPolicy
    ) -> None:
        provider = make_provider(policy=permissive_policy)
        spoken = provider.respond(make_transcript("analyze gold"))
        assert 0 < len(spoken) <= MAX_SPOKEN_CHARS

    def test_a_maximum_length_rationale_is_still_spoken_in_full(
        self, permissive_policy: RiskPolicy
    ) -> None:
        # `ReasoningVerdict.rationale` is capped at 400 characters, so the
        # longest one the schema permits still fits comfortably inside the
        # spoken budget and must not be dropped.
        longest = ("Momentum is constructive and the structure is intact. " * 8)[:400].strip()
        provider = make_provider(
            policy=permissive_policy,
            reasoning_provider=FakeReasoningProvider(rationale=longest),
        )
        spoken = provider.respond(make_transcript("analyze gold"))

        assert len(spoken) <= MAX_SPOKEN_CHARS
        assert longest in spoken

    def test_omits_the_rationale_rather_than_cutting_a_sentence_in_half(
        self, permissive_policy: RiskPolicy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The budget is generous enough today that this branch is not
        # reachable through the schema's own 400-character cap. It exists so
        # a future synthesiser with a tighter limit degrades by dropping the
        # model's sentence whole, never by speaking half of one — which is
        # only worth having if it is actually exercised.
        from orion.orchestrator import response as response_module

        rationale = "Momentum is constructive and the structure is intact."
        provider = make_provider(
            policy=permissive_policy,
            reasoning_provider=FakeReasoningProvider(rationale=rationale),
        )
        monkeypatch.setattr(response_module, "MAX_SPOKEN_CHARS", 260)
        spoken = provider.respond(make_transcript("analyze gold"))

        assert rationale not in spoken
        # Nothing was cut mid-sentence: what remains is whole.
        assert spoken.endswith(".")
        assert "trend is bullish" in spoken

    def test_speaks_no_number_that_no_engine_produced(self, permissive_policy: RiskPolicy) -> None:
        provider = make_provider(policy=permissive_policy)
        spoken = provider.respond(make_transcript("analyze gold"))
        report = provider.last_report
        assert report is not None

        assert report.evidence.indicators.rsi is not None
        permitted = {
            f"{report.evidence.indicators.rsi:.1f}",
            f"{report.reasoning.confidence * 100:.0f}",
        }
        # Every digit run in the utterance must be one of the two figures
        # the engines computed — nothing else numeric may be spoken.
        import re

        for number in re.findall(r"\d+(?:\.\d+)?", spoken):
            assert number in permitted, f"unexplained number {number!r} in: {spoken}"
