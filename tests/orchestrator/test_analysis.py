"""The composition root, exercised against the real engines.

Nothing between `fetch_ohlcv` and the finished report is stubbed: real
validation, real indicators, real structure, the real Decision Engine and
the real Risk Guardian all run on every test in this file. Only the two
network boundaries (market data, reasoning) and the portfolio store are
doubles.

The load-bearing assertions are about what the orchestrator refuses to do:
never invent a paper account, never write to one, never analyse data that
failed validation, never swallow a provider failure, and never let a spoken
number appear that no engine produced.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from orion.data.models import Timeframe
from orion.data.provider_base import MarketDataProviderError
from orion.orchestrator.analysis import (
    DEFAULT_OUTPUT_SIZE,
    MACRO_NOT_CONNECTED,
    NEWS_NOT_CONNECTED,
    ORCHESTRATOR_VERSION,
    AnalysisDataQualityError,
    AnalysisError,
    AnalysisService,
    build_analysis_id,
    run_analysis,
)
from orion.orchestrator.intent import parse_intent
from orion.orchestrator.schemas import (
    AnalysisReport,
    CapabilityStatus,
    Intent,
    IntentKind,
    MacroAvailability,
    RiskAvailability,
)
from orion.paper.store import PortfolioStoreError
from orion.reasoning.provider_base import ReasoningProviderError
from orion.reasoning.schemas import Action, Direction
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.schemas import RiskOutcome

from .conftest import (
    LAST_BAR_TIME,
    NOW,
    FakeMarketDataProvider,
    FakePortfolioStore,
    FakeReasoningProvider,
    make_bars,
    make_portfolio,
    make_snapshot,
)

ANALYZE_GOLD = parse_intent("analyze gold")


def analyse(
    *,
    policy: RiskPolicy,
    market_data: FakeMarketDataProvider | None = None,
    reasoning_provider: FakeReasoningProvider | None = None,
    portfolio_store: FakePortfolioStore | None = None,
    intent: Intent = ANALYZE_GOLD,
    now: datetime = NOW,
    **kwargs: object,
) -> AnalysisReport:
    return run_analysis(
        intent=intent,
        market_data=market_data or FakeMarketDataProvider(),
        reasoning_provider=reasoning_provider or FakeReasoningProvider(),
        policy=policy,
        portfolio_store=portfolio_store,
        now=now,
        **kwargs,  # type: ignore[arg-type]
    )


class TestTheHappyPath:
    def test_produces_a_complete_report_from_the_real_engines(
        self, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(
            policy=permissive_policy, portfolio_store=FakePortfolioStore(make_portfolio())
        )

        assert report.symbol == "XAUUSD"
        assert report.timeframe is Timeframe.H1
        assert report.analyzed_at == NOW
        assert report.orchestrator_version == ORCHESTRATOR_VERSION

        # Every one of the brief's seven fields is reachable, and the four
        # that have a source carry values the engines actually computed.
        assert report.evidence.structure.trend.value == "bullish"
        assert report.evidence.indicators.rsi is not None
        assert 0.0 <= report.reasoning.confidence <= 1.0
        assert report.decision.action is Action.BUY
        assert report.risk.availability is RiskAvailability.AVAILABLE
        assert report.news.status is CapabilityStatus.NOT_CONNECTED
        assert report.macro.availability is MacroAvailability.NOT_CONNECTED

    def test_asks_the_data_provider_for_exactly_what_the_intent_named(
        self, permissive_policy: RiskPolicy
    ) -> None:
        market_data = FakeMarketDataProvider()
        analyse(policy=permissive_policy, market_data=market_data)

        assert market_data.calls == [("XAUUSD", Timeframe.H1, DEFAULT_OUTPUT_SIZE)]

    def test_reasons_over_the_evidence_it_built(self, permissive_policy: RiskPolicy) -> None:
        reasoning_provider = FakeReasoningProvider()
        report = analyse(policy=permissive_policy, reasoning_provider=reasoning_provider)

        assert len(reasoning_provider.calls) == 1
        assert reasoning_provider.calls[0] == report.evidence

    def test_is_deterministic(self, permissive_policy: RiskPolicy) -> None:
        first = analyse(
            policy=permissive_policy, portfolio_store=FakePortfolioStore(make_portfolio())
        )
        second = analyse(
            policy=permissive_policy, portfolio_store=FakePortfolioStore(make_portfolio())
        )
        assert first == second

    def test_the_analysis_id_is_derived_from_the_bar_not_the_wall_clock(
        self, permissive_policy: RiskPolicy
    ) -> None:
        early = analyse(policy=permissive_policy, now=NOW)
        later = analyse(policy=permissive_policy, now=NOW + timedelta(minutes=20))

        # Same bar, same question, same identifier — no uuid4() anywhere.
        assert early.analysis_id == later.analysis_id
        assert early.analysis_id == build_analysis_id("XAUUSD", Timeframe.H1, LAST_BAR_TIME)
        assert "XAUUSD" in early.analysis_id

    def test_a_wait_proposal_is_a_complete_analysis_not_a_failure(
        self, permissive_policy: RiskPolicy
    ) -> None:
        waiting = FakeReasoningProvider(
            action=Action.WAIT, direction=Direction.NONE, stop_loss=None, take_profit=None
        )
        report = analyse(
            policy=permissive_policy,
            reasoning_provider=waiting,
            portfolio_store=FakePortfolioStore(make_portfolio()),
        )

        assert report.decision.action is Action.WAIT
        assert report.risk.verdict is not None
        assert report.risk.verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED

    def test_the_default_kill_switch_vetoes_and_still_reports_everything(
        self, blocking_policy: RiskPolicy
    ) -> None:
        report = analyse(
            policy=blocking_policy, portfolio_store=FakePortfolioStore(make_portfolio())
        )

        assert report.risk.verdict is not None
        assert report.risk.verdict.outcome is RiskOutcome.VETOED
        # A veto is an answer, not an error: the rest of the report stands.
        assert report.evidence.indicators.rsi is not None
        assert report.reasoning.rationale


class TestBadRequests:
    def test_refuses_an_intent_it_was_not_given(self, permissive_policy: RiskPolicy) -> None:
        with pytest.raises(AnalysisError):
            analyse(policy=permissive_policy, intent=Intent(kind=IntentKind.UNKNOWN))

    def test_refuses_a_naive_now(self, permissive_policy: RiskPolicy) -> None:
        with pytest.raises(AnalysisError):
            analyse(policy=permissive_policy, now=datetime(2026, 8, 28, 12, 5))

    def test_refuses_a_non_positive_output_size(self, permissive_policy: RiskPolicy) -> None:
        for size in (0, -1, -200):
            with pytest.raises(AnalysisError):
                analyse(policy=permissive_policy, output_size=size)

    def test_never_reaches_the_network_for_a_request_it_refuses(
        self, permissive_policy: RiskPolicy
    ) -> None:
        market_data = FakeMarketDataProvider()
        with pytest.raises(AnalysisError):
            analyse(
                policy=permissive_policy,
                market_data=market_data,
                intent=Intent(kind=IntentKind.UNKNOWN),
            )
        assert market_data.calls == []


class TestFailClosed:
    def test_a_market_data_failure_aborts_the_analysis(self, permissive_policy: RiskPolicy) -> None:
        market_data = FakeMarketDataProvider(error=MarketDataProviderError("upstream is down"))
        with pytest.raises(MarketDataProviderError):
            analyse(policy=permissive_policy, market_data=market_data)

    def test_a_reasoning_failure_aborts_the_analysis(self, permissive_policy: RiskPolicy) -> None:
        reasoning_provider = FakeReasoningProvider(error=ReasoningProviderError("model refused"))
        with pytest.raises(ReasoningProviderError):
            analyse(policy=permissive_policy, reasoning_provider=reasoning_provider)

    @pytest.mark.parametrize(
        ("label", "snapshot_kwargs"),
        [
            ("empty", {"bars": []}),
            ("gapped", {"bars": make_bars()[:40] + make_bars()[60:]}),
        ],
    )
    def test_unusable_market_data_is_refused_rather_than_analysed(
        self, permissive_policy: RiskPolicy, label: str, snapshot_kwargs: dict[str, object]
    ) -> None:
        market_data = FakeMarketDataProvider(make_snapshot(**snapshot_kwargs))  # type: ignore[arg-type]
        with pytest.raises(AnalysisDataQualityError):
            analyse(policy=permissive_policy, market_data=market_data)

    def test_stale_market_data_is_refused(self, permissive_policy: RiskPolicy) -> None:
        # Four hours past the last hourly bar, against a three-bar threshold.
        with pytest.raises(AnalysisDataQualityError):
            analyse(policy=permissive_policy, now=LAST_BAR_TIME + timedelta(hours=4))

    def test_the_data_quality_message_names_only_issue_codes(
        self, permissive_policy: RiskPolicy
    ) -> None:
        market_data = FakeMarketDataProvider(make_snapshot(bars=[]))
        with pytest.raises(AnalysisDataQualityError) as caught:
            analyse(policy=permissive_policy, market_data=market_data)

        message = str(caught.value)
        assert "empty" in message
        # No finding details: those embed timestamps and vary run to run.
        assert "snapshot has no bars" not in message
        assert "2026" not in message

    def test_a_reasoning_failure_leaves_the_portfolio_store_untouched(
        self, permissive_policy: RiskPolicy
    ) -> None:
        store = FakePortfolioStore(make_portfolio())
        with pytest.raises(ReasoningProviderError):
            analyse(
                policy=permissive_policy,
                reasoning_provider=FakeReasoningProvider(error=ReasoningProviderError("no")),
                portfolio_store=store,
            )
        assert store.load_calls == 0


class TestRiskIsNeverFabricated:
    def test_no_store_means_no_verdict_and_says_so(self, permissive_policy: RiskPolicy) -> None:
        report = analyse(policy=permissive_policy, portfolio_store=None)

        assert report.risk.availability is RiskAvailability.NO_PORTFOLIO_STORE
        assert report.risk.verdict is None
        assert report.risk.portfolio_as_of is None

    def test_an_uninitialised_account_means_no_verdict(self, permissive_policy: RiskPolicy) -> None:
        report = analyse(policy=permissive_policy, portfolio_store=FakePortfolioStore(None))

        assert report.risk.availability is RiskAvailability.PORTFOLIO_UNAVAILABLE
        assert report.risk.verdict is None

    def test_an_unreadable_store_means_no_verdict(self, permissive_policy: RiskPolicy) -> None:
        store = FakePortfolioStore(error=PortfolioStoreError("could not open ./orion.db"))
        report = analyse(policy=permissive_policy, portfolio_store=store)

        assert report.risk.availability is RiskAvailability.PORTFOLIO_UNAVAILABLE
        assert report.risk.verdict is None

    def test_an_account_for_another_market_means_no_verdict(
        self, permissive_policy: RiskPolicy
    ) -> None:
        store = FakePortfolioStore(make_portfolio(symbol="EURUSD"))
        report = analyse(policy=permissive_policy, portfolio_store=store)

        assert report.risk.availability is RiskAvailability.PORTFOLIO_SYMBOL_MISMATCH
        assert report.risk.verdict is None
        # The stored record's own timestamp is still reported honestly.
        assert report.risk.portfolio_as_of is not None

    def test_an_account_timestamped_in_the_future_means_no_verdict(
        self, permissive_policy: RiskPolicy
    ) -> None:
        future = make_portfolio(as_of=NOW + timedelta(hours=1))
        report = analyse(policy=permissive_policy, portfolio_store=FakePortfolioStore(future))

        assert report.risk.availability is RiskAvailability.PORTFOLIO_UNAVAILABLE
        assert report.risk.verdict is None

    def test_reports_the_stored_timestamp_not_the_marked_projection(
        self, permissive_policy: RiskPolicy
    ) -> None:
        stored_at = LAST_BAR_TIME - timedelta(hours=1)
        store = FakePortfolioStore(make_portfolio(as_of=stored_at))
        report = analyse(policy=permissive_policy, portfolio_store=store)

        assert report.risk.availability is RiskAvailability.AVAILABLE
        # Marking to market moves `as_of` to now; the panel must still show
        # how old the underlying record really was.
        assert report.risk.portfolio_as_of == stored_at
        assert report.risk.verdict is not None
        assert report.risk.verdict.reviewed_at == NOW

    def test_every_unavailable_path_still_produces_the_rest_of_the_analysis(
        self, permissive_policy: RiskPolicy
    ) -> None:
        for store in (
            None,
            FakePortfolioStore(None),
            FakePortfolioStore(error=PortfolioStoreError("x")),
            FakePortfolioStore(make_portfolio(symbol="EURUSD")),
        ):
            report = analyse(policy=permissive_policy, portfolio_store=store)
            assert report.evidence.structure.trend.value == "bullish"
            assert report.decision.action is Action.BUY
            assert report.reasoning.confidence == 0.55


class TestZeroPersistence:
    def test_never_writes_to_the_portfolio_store(self, permissive_policy: RiskPolicy) -> None:
        # `FakePortfolioStore.save`/`initialize` raise AssertionError, so a
        # single write anywhere in the chain fails this outright.
        store = FakePortfolioStore(make_portfolio())
        analyse(policy=permissive_policy, portfolio_store=store)
        assert store.load_calls == 1

    def test_reads_the_account_exactly_once_per_analysis(
        self, permissive_policy: RiskPolicy
    ) -> None:
        store = FakePortfolioStore(make_portfolio())
        analyse(policy=permissive_policy, portfolio_store=store)
        analyse(policy=permissive_policy, portfolio_store=store)
        assert store.load_calls == 2


class TestAbsentCapabilities:
    def test_news_is_always_reported_as_not_connected(self, permissive_policy: RiskPolicy) -> None:
        report = analyse(policy=permissive_policy)

        assert report.news.status is CapabilityStatus.NOT_CONNECTED
        assert report.news.detail == NEWS_NOT_CONNECTED
        assert "no news source is connected" in report.news.detail.lower()

    def test_macro_is_not_connected_when_no_calendar_is_wired(
        self, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(policy=permissive_policy)

        assert report.macro.availability is MacroAvailability.NOT_CONNECTED
        assert report.macro.detail == MACRO_NOT_CONNECTED
        assert report.macro.opinion is None

    def test_the_absent_panels_carry_no_data_of_any_kind(
        self, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(policy=permissive_policy)

        news = report.news.model_dump()
        assert set(news) == {"status", "detail"}
        assert isinstance(news["detail"], str)
        # No headline, no number, no timestamp, nowhere to put one.
        assert not any(char.isdigit() for char in news["detail"])

        # Macro now *has* somewhere to put a reading, so the guarantee is
        # sharper rather than weaker: with nothing wired, that place is empty.
        macro = report.macro.model_dump()
        assert set(macro) == {"availability", "detail", "opinion"}
        assert macro["opinion"] is None
        assert not any(char.isdigit() for char in macro["detail"])


class TestAnalysisService:
    def test_runs_the_same_analysis_as_the_function(self, permissive_policy: RiskPolicy) -> None:
        market_data = FakeMarketDataProvider()
        reasoning_provider = FakeReasoningProvider()
        store = FakePortfolioStore(make_portfolio())

        direct = run_analysis(
            intent=ANALYZE_GOLD,
            market_data=market_data,
            reasoning_provider=reasoning_provider,
            policy=permissive_policy,
            portfolio_store=store,
            now=NOW,
        )
        service = AnalysisService(
            market_data=FakeMarketDataProvider(),
            reasoning_provider=FakeReasoningProvider(),
            policy=permissive_policy,
            portfolio_store=FakePortfolioStore(make_portfolio()),
        )
        assert service.run(ANALYZE_GOLD, now=NOW) == direct

    def test_defaults_to_no_portfolio_store(self, permissive_policy: RiskPolicy) -> None:
        service = AnalysisService(
            market_data=FakeMarketDataProvider(),
            reasoning_provider=FakeReasoningProvider(),
            policy=permissive_policy,
        )
        report = service.run(ANALYZE_GOLD, now=NOW)
        assert report.risk.availability is RiskAvailability.NO_PORTFOLIO_STORE

    def test_holds_no_state_between_runs(self, permissive_policy: RiskPolicy) -> None:
        service = AnalysisService(
            market_data=FakeMarketDataProvider(),
            reasoning_provider=FakeReasoningProvider(),
            policy=permissive_policy,
        )
        assert service.run(ANALYZE_GOLD, now=NOW) == service.run(ANALYZE_GOLD, now=NOW)

    def test_passes_its_configured_output_size_through(self, permissive_policy: RiskPolicy) -> None:
        market_data = FakeMarketDataProvider()
        AnalysisService(
            market_data=market_data,
            reasoning_provider=FakeReasoningProvider(),
            policy=permissive_policy,
            output_size=75,
        ).run(ANALYZE_GOLD, now=NOW)
        assert market_data.calls == [("XAUUSD", Timeframe.H1, 75)]


def test_uses_the_policys_own_staleness_threshold_not_a_second_number() -> None:
    # A policy that tolerates far more staleness must actually change what
    # the orchestrator accepts — proving the threshold is read from the
    # policy rather than hardcoded alongside it.
    tolerant = RiskPolicy(kill_switch=KillSwitchState.DISENGAGED, max_staleness_bars=48.0)
    strict = RiskPolicy(kill_switch=KillSwitchState.DISENGAGED, max_staleness_bars=1.0)
    late = LAST_BAR_TIME + timedelta(hours=6)

    assert analyse(policy=tolerant, now=late).evidence.as_of == LAST_BAR_TIME
    with pytest.raises(AnalysisDataQualityError):
        analyse(policy=strict, now=late)


def test_never_reads_a_clock() -> None:
    """Every timestamp in a report traces back to an argument or to the
    market data — never to a clock read inside the orchestrator.

    AST-parsed rather than text-searched, so the module docstrings that
    *discuss* not calling `datetime.now()` do not fail their own rule.
    """
    import ast
    from pathlib import Path

    import orion.orchestrator

    package = Path(orion.orchestrator.__file__ or "").parent
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"now", "utcnow", "today", "time", "monotonic"}
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.func.attr}()")
    assert not offenders, f"the orchestrator reads a clock: {offenders}"
