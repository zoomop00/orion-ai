"""The one file in `orion.orchestrator` allowed to read configuration and
construct concrete vendor providers.

Everything else in this package takes its dependencies as arguments and is
constructible with no environment at all — that is what makes `run_analysis`
testable against real engines and fake boundaries. But something, somewhere,
has to turn `Settings` into objects, and the alternative to doing it here is
doing it in `orion.interface`, which
`tests/interface/test_import_hygiene.py::test_orion_interface_imports_no_
mission_0_8_pipeline_package` forbids outright and for a good reason: "the
moment it can reach `orion.decision`/`orion.risk`/`orion.paper`, an HTTP
handler is one edit away from making trading decisions."

So the wiring lives on this side of that boundary, in one file, with its own
carve-out in `tests/orchestrator/test_import_hygiene.py` — the same per-file
allowlist idiom `tests/voice/test_import_hygiene.py` already uses for
`sounddevice` and the frontend's source-safety test uses for `fetch`. Every
other module in this package still may not import `orion.config`, and a test
says so.

BUILT FRESH, PER ANALYSIS, ON PURPOSE. `build_analysis_service` is a factory
rather than a singleton because `orion.risk.policy.RiskPolicy.from_settings`
documents the requirement explicitly: "Call this once per analysis cycle from
a fresh `get_settings()` (which is deliberately uncached) rather than caching
a `RiskPolicy` for the process lifetime — that is what gives an operator
editing `.env` near-real-time kill-switch response with zero new state." A
process-lifetime `RiskPolicy` would mean engaging the kill switch required a
restart. It does not.

MISSING CREDENTIALS ARE NOT AN ERROR HERE. `None` means "this deployment
cannot analyse", and the caller falls back to the Mission 9 response provider
that has always been there — exactly the same degradation `voice_available`
already expresses for Deepgram. Raising instead would make a machine with no
Twelve Data key unable to serve a plain conversational turn, which was never
true before and must not become true now.

NO PAPER ACCOUNT IS OPENED. `portfolio_store` is a parameter, defaulted to
`None`, and this module never constructs a `SqlitePortfolioStore`.
`orion.memory.engine.create_memory_engine` runs `metadata.create_all(engine)`
— it *creates a database file and its tables* — and the HTTP surface this
feeds documents zero persistence as a load-bearing property. The orchestrator
already accepts a real `PortfolioStore` and Phase 5.1 tests every branch of
it; handing one in is a deliberate deployment decision, not something a
composition root should do as a side effect of starting up. Until it is made,
the risk panel reports `NO_PORTFOLIO_STORE` — honestly, and never a
fabricated flat account.
"""

from __future__ import annotations

from orion.analysts.claude_macro_provider import ClaudeMacroAnalystProvider
from orion.config import Settings
from orion.data.twelvedata_provider import TwelveDataProvider
from orion.orchestrator.analysis import DEFAULT_OUTPUT_SIZE, AnalysisService
from orion.paper.store import PortfolioStore
from orion.reasoning.claude_provider import ClaudeReasoningProvider
from orion.research.finnhub_provider import FinnhubEconomicCalendarProvider
from orion.risk.policy import RiskPolicy


def analysis_available(settings: Settings) -> bool:
    """Whether the credentials one analysis needs are configured.

    Booleans only — never a key, never its length, never a prefix, matching
    `orion.interface.schemas.HealthResponse.voice_available`'s identical
    discipline.

    `SecretStr` is tested for truthiness rather than unwrapped: an empty one is
    already falsy, so answering "is this configured?" never has to materialise
    the credential at all.
    """
    return bool(settings.twelve_data_api_key) and bool(settings.anthropic_api_key)


def macro_available(settings: Settings) -> bool:
    """Whether this deployment can read and assess the economic calendar.

    A third credential on top of the two an analysis already needs, and
    genuinely optional: without it every analysis still completes, and the
    macro panel reports `not_connected` exactly as every build before this
    one did. Booleans only, and `SecretStr` is tested for truthiness rather
    than unwrapped -- same discipline as `analysis_available`.
    """
    return bool(settings.finnhub_api_key) and bool(settings.anthropic_api_key)


def build_analysis_service(
    settings: Settings,
    *,
    portfolio_store: PortfolioStore | None = None,
    output_size: int = DEFAULT_OUTPUT_SIZE,
) -> AnalysisService | None:
    """Construct one request-scoped `AnalysisService`, or `None`.

    Returns `None` — never a partially-wired service and never a service with
    a placeholder provider — when either credential is missing. See the module
    docstring for why that is a degradation rather than a failure.

    Every object built here is new. Nothing is cached on a module global, and
    the vendor HTTP/SDK clients each provider creates belong to that provider
    and die with it, matching `orion.interface.service.VoiceTurnService`'s
    identical one-client-per-request posture.
    """
    if not analysis_available(settings):
        return None

    # `.get_secret_value()` appears exactly here and as the argument itself:
    # the plain credential is never bound to a local, never stored on this
    # module, and exists only for as long as the provider's constructor takes
    # to hand it to its HTTP client / SDK.
    # Built together or not at all. A calendar with no analyst, or an
    # analyst with no calendar, is a half-wired capability that would report
    # `not_connected` anyway -- so the pair is the unit, and `macro_available`
    # is the one place that decides.
    calendar_provider = None
    macro_analyst = None
    if macro_available(settings):
        calendar_provider = FinnhubEconomicCalendarProvider(
            settings.finnhub_api_key.get_secret_value(), settings.finnhub_base_url
        )
        macro_analyst = ClaudeMacroAnalystProvider(
            settings.anthropic_api_key.get_secret_value(),
            settings.anthropic_model,
            settings.anthropic_effort,
        )

    return AnalysisService(
        market_data=TwelveDataProvider(
            settings.twelve_data_api_key.get_secret_value(), settings.twelve_data_base_url
        ),
        reasoning_provider=ClaudeReasoningProvider(
            settings.anthropic_api_key.get_secret_value(),
            settings.anthropic_model,
            settings.anthropic_effort,
        ),
        # Fresh per call, deliberately. See the module docstring.
        policy=RiskPolicy.from_settings(settings),
        portfolio_store=portfolio_store,
        output_size=output_size,
        calendar_provider=calendar_provider,
        macro_analyst=macro_analyst,
    )
