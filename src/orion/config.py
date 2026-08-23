"""Central configuration for ORION.

All external identifiers that might need to change without a code change live
here: which AI model to call, how hard it should reason, which market data
provider to use, where the decision journal lives, and the Risk Guardian's
operating limits (kill switch, staleness/decision-age tolerances, minimum
risk/reward, volatility policy). Nothing downstream of this module should
read `os.environ` directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from orion.data.validation import DEFAULT_MAX_STALENESS_BARS

ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]

# The KillSwitchState enum lives in orion.risk.policy, not here — importing it
# would make orion.config depend on orion.risk, which itself must depend on
# orion.config (RiskPolicy.from_settings reads Settings). This literal is the
# wire-level equivalent of ReasoningEffort's pattern: Settings only ever knows
# about plain strings; orion.risk.policy maps this one into its own enum.
KillSwitch = Literal["engaged", "disengaged"]


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- AI reasoning provider ---
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Model identifier is configuration only. Changing the recommended Claude
    # model must never require touching orion.reasoning.claude_provider — only
    # this default (or the ANTHROPIC_MODEL environment variable) changes.
    # See docs/DECISIONS.md for why claude-opus-5 is the current default.
    anthropic_model: str = Field(default="claude-opus-5", alias="ANTHROPIC_MODEL")

    # Reasoning depth is an independent axis from the model identifier — the
    # Anthropic API exposes it as its own request parameter (output_config.effort).
    anthropic_effort: ReasoningEffort = Field(default="medium", alias="ANTHROPIC_EFFORT")

    # --- Market data provider ---
    twelve_data_api_key: str = Field(default="", alias="TWELVE_DATA_API_KEY")
    twelve_data_base_url: str = Field(
        default="https://api.twelvedata.com", alias="TWELVE_DATA_BASE_URL"
    )

    # --- Decision memory ---
    orion_db_path: str = Field(default="./orion.db", alias="ORION_DB_PATH")

    # --- Risk Guardian ---
    # Fail-safe default is ENGAGED (blocking): an absent or misconfigured
    # switch must never silently permit trading. An invalid value (anything
    # other than "engaged"/"disengaged") fails Settings construction rather
    # than falling back to a guess — see docs/DECISIONS.md.
    orion_kill_switch: KillSwitch = Field(default="engaged", alias="ORION_KILL_SWITCH")

    # Same figure orion.data.validation already uses to classify stale bars
    # at fetch time; the Risk Guardian re-checks staleness at review time
    # (after the reasoning round-trip) using this identical threshold, not a
    # second independently-chosen number.
    orion_max_staleness_bars: float = Field(
        default=DEFAULT_MAX_STALENESS_BARS, gt=0, alias="ORION_MAX_STALENESS_BARS"
    )

    # How many bars old a ProposedDecision itself may be before the Risk
    # Guardian treats it as expired. A decision computed from bar N is only
    # meaningful until bar N+1 exists.
    orion_max_decision_age_bars: float = Field(
        default=1.0, gt=0, alias="ORION_MAX_DECISION_AGE_BARS"
    )

    # 1.0 is a mathematically justified floor (below it, breakeven requires a
    # win rate over 50%, and no win-rate estimate exists anywhere in this
    # repo) — not an empirically validated trading-strategy parameter. Raising
    # it is a legitimate policy choice but requires real historical
    # validation to justify a specific higher number; see docs/DECISIONS.md.
    orion_min_risk_reward: float = Field(default=1.0, ge=1.0, alias="ORION_MIN_RISK_REWARD")

    # Off by default: the existing ATR% volatility thresholds are documented
    # (orion.analysis.structure, docs/DECISIONS.md) as uncalibrated
    # placeholders, and blocking HIGH by default would systematically veto
    # most higher-timeframe proposals using a number nobody calibrated. This
    # flag is the explicit, recorded opt-in for turning that circuit breaker
    # on before Mission 6 provides real calibration.
    orion_block_high_volatility: bool = Field(default=False, alias="ORION_BLOCK_HIGH_VOLATILITY")

    # --- Risk Guardian: Mission 4 portfolio-state-dependent limits ---
    # See docs/DECISIONS.md (Mission 4 architecture entry) and
    # orion.risk.policy.RiskPolicy for why these are conventional engineering
    # starting points, not empirically calibrated to XAUUSD.
    orion_max_risk_per_trade: float = Field(
        default=0.01, gt=0, le=1.0, alias="ORION_MAX_RISK_PER_TRADE"
    )
    orion_max_exposure: float = Field(default=1.0, gt=0, le=1.0, alias="ORION_MAX_EXPOSURE")
    orion_max_daily_loss: float = Field(default=0.03, gt=0, le=1.0, alias="ORION_MAX_DAILY_LOSS")
    orion_max_weekly_loss: float = Field(default=0.08, gt=0, le=1.0, alias="ORION_MAX_WEEKLY_LOSS")
    orion_max_drawdown: float = Field(default=0.15, gt=0, le=1.0, alias="ORION_MAX_DRAWDOWN")
    orion_max_portfolio_staleness_bars: float = Field(
        default=DEFAULT_MAX_STALENESS_BARS, gt=0, alias="ORION_MAX_PORTFOLIO_STALENESS_BARS"
    )

    # --- Paper Trading (Mission 4) ---
    # Deterministic simulated-execution parameters only — never a risk
    # limit. See orion.paper.policy.PaperExecutionPolicy for why this is a
    # separate config object from the Risk Guardian's own settings above.
    paper_starting_balance: float = Field(default=10_000.0, gt=0, alias="PAPER_STARTING_BALANCE")
    paper_spread_bps: float = Field(default=2.0, ge=0, alias="PAPER_SPREAD_BPS")
    paper_slippage_bps: float = Field(default=1.0, ge=0, alias="PAPER_SLIPPAGE_BPS")
    paper_fee_bps: float = Field(default=1.0, ge=0, alias="PAPER_FEE_BPS")


def get_settings() -> Settings:
    """Load settings fresh from the environment.

    Not cached: tests construct their own `Settings(...)` instances directly
    rather than relying on process-wide state.
    """
    return Settings()
