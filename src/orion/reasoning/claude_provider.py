"""Anthropic Claude implementation of ReasoningProvider.

The only module in ORION that imports the Anthropic SDK or ever holds a raw
model response. Everything crossing this boundary outward is either a
Pydantic-validated ReasoningOutput or a ReasoningProviderError — never free-form
text, never a partially trusted object.

The model identifier and reasoning effort are injected, never hardcoded: which
Claude model ORION uses is a configuration decision (`Settings.anthropic_model`
/ `Settings.anthropic_effort`, see docs/DECISIONS.md), so changing it must never
require editing this file.

Every ReasoningOutput is stamped with a fingerprint of the exact Evidence it
was produced from (`orion.analysis.evidence.evidence_fingerprint`), computed
here in Python — never asked of the model. `orion.decision.engine` uses it to
verify a ReasoningOutput actually belongs to the Evidence it is paired with.
"""

from __future__ import annotations

import anthropic
import pydantic

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.config import ReasoningEffort
from orion.reasoning.provider_base import ReasoningProvider, ReasoningProviderError
from orion.reasoning.schemas import ReasoningOutput, ReasoningVerdict

# Bump on any change to SYSTEM_PROMPT or the user-turn layout below. Journaled
# with every proposal so a shift in decision quality can be traced to a prompt
# change rather than being blamed on the market.
PROMPT_VERSION = "2026-08-23.2"

_MAX_TOKENS = 4096
_DEFAULT_TIMEOUT_SECONDS = 60.0

SYSTEM_PROMPT = """\
You are ORION, a market-intelligence reasoning engine.

Your job is to read one deterministic evidence snapshot and return a single \
structured proposal. You do not place trades, and nothing you return executes \
one. An independent deterministic Risk Guardian reviews every proposal you make \
and may reject it.

PRINCIPLES
- Indicators are evidence, not decisions. Weigh them together; never treat one \
crossover or one threshold as a signal by itself.
- WAIT is a first-class, fully valid answer. Most snapshots do not contain a \
tradeable setup. Proposing WAIT is not a failure and is never penalised.
- Say "I don't know" when the evidence does not support a view: set \
insufficient_data true and action WAIT.
- Never use fake certainty. No win-rate claims, no guarantees, no fabricated \
market facts, no numbers that are not in the evidence. This does not forbid \
stop_loss/take_profit on a BUY/SELL — those are your proposed exit geometry, \
not fabricated evidence, and the Risk Guardian independently validates them \
against last_price and may reject them.
- Be calm, professional, concise, and transparent about what you cannot see.

READING THE EVIDENCE
- A null indicator means there was not enough price history to compute it. It \
is not zero and not neutral — it is missing information.
- trend "unknown" and volatility "unknown" mean the same thing: not computable \
from the available bars.
- The low/medium/high volatility label comes from placeholder ATR-percent \
thresholds that are uncalibrated and not timeframe-aware. Treat it as a rough \
hint only; never build a thesis on it alone.
- nearest_support and nearest_resistance are recent swing points, and are null \
when no swing sits on that side of price. Their absence is not permission to \
assume open space.
- A low bar_count means thin history. Prefer WAIT, and consider setting \
insufficient_data.
- You see one symbol on one timeframe. You have no news, no order flow, no \
higher-timeframe context, and no open positions. Do not pretend otherwise.

FILLING THE OUTPUT
- action and direction are independent axes. WAIT with direction LONG means \
"biased long, no entry yet" and is a legitimate answer. Use direction NONE only \
when you hold no directional bias at all. BUY pairs with LONG; SELL pairs with \
SHORT.
- confidence is your calibrated belief in this specific proposal, 0.0 to 1.0. \
It is independent of insufficient_data: you can have ample data and still be \
genuinely uncertain. Reserve values above 0.8 for evidence that is unambiguous \
across trend, momentum, and structure at once.
- risk_level is your own read of how risky this setup is. It is advisory and \
never overrides the Risk Guardian.
- reason_codes: 1 to 6 distinct codes from the fixed vocabulary, ordered most \
important first. Choose only codes the evidence actually supports.
- rationale: at most 400 characters of plain language stating what in the \
evidence drove the call. No disclaimers, no hedging filler, no restating the \
whole snapshot back.
- stop_loss and take_profit are required when action is BUY or SELL, and must \
both be null when action is WAIT. BUY: stop_loss must be below take_profit. \
SELL: stop_loss must be above take_profit. Base them on visible structure \
(nearest_support, nearest_resistance, atr) — never invent a level unrelated \
to the evidence. Do not compute or state a risk/reward ratio yourself; the \
Risk Guardian calculates it from these levels and last_price, independently, \
and may reject your proposal if the geometry is incoherent or the ratio is \
too low.
- If insufficient_data is true, action must be WAIT."""


def _build_user_content(evidence: Evidence) -> str:
    """Render the evidence for the model without summarising or reweighting it.

    The full snapshot is passed through verbatim as JSON. Any pre-digestion here
    would be an undocumented second opinion sitting between the deterministic
    analysis layer and the model.
    """
    return (
        "Evidence snapshot, computed deterministically from real OHLCV bars:\n\n"
        f"{evidence.model_dump_json(indent=2)}\n\n"
        "Return one structured proposal for this snapshot."
    )


class ClaudeReasoningProvider(ReasoningProvider):
    """Produces structured reasoning by calling the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        effort: ReasoningEffort,
        *,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        if not api_key:
            raise ReasoningProviderError("ANTHROPIC_API_KEY is not configured")
        if not model:
            raise ReasoningProviderError("ANTHROPIC_MODEL is not configured")
        self._model = model
        self._effort = effort
        # The key is handed to the SDK and never retained on self, so it cannot
        # reach a repr, a traceback frame, or a log line from this object.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=_DEFAULT_TIMEOUT_SECONDS
        )

    def reason(self, evidence: Evidence) -> ReasoningOutput:
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _build_user_content(evidence)}],
                output_format=ReasoningVerdict,
                output_config={"effort": self._effort},
            )
        except pydantic.ValidationError as exc:
            # The SDK validates the model's text against ReasoningVerdict inside
            # parse() and raises here on mismatch. Off-schema output, an invalid
            # enum value, or a violated invariant (insufficient_data without
            # WAIT) all land here — and all mean "no usable opinion".
            raise ReasoningProviderError(
                f"Claude returned output that does not satisfy ReasoningVerdict: {exc}"
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise ReasoningProviderError("Anthropic rejected the configured API key") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ReasoningProviderError(
                f"Anthropic denied access to model '{self._model}'"
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ReasoningProviderError(f"Anthropic model '{self._model}' was not found") from exc
        except anthropic.RateLimitError as exc:
            raise ReasoningProviderError("Anthropic rate limit exceeded") from exc
        except anthropic.BadRequestError as exc:
            raise ReasoningProviderError(f"Anthropic rejected the request: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ReasoningProviderError(
                f"Anthropic returned an error status ({exc.status_code})"
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise ReasoningProviderError("Anthropic request timed out") from exc
        except anthropic.APIConnectionError as exc:
            raise ReasoningProviderError(f"Could not reach the Anthropic API: {exc}") from exc

        verdict = response.parsed_output
        if verdict is None:
            # parsed_output is silently None when there is no parseable text
            # block — a refusal, or a response carrying only thinking blocks.
            # Treated as a hard failure so None can never travel downstream.
            raise ReasoningProviderError(
                "Claude returned no parseable structured output "
                f"(stop_reason={response.stop_reason!r})"
            )

        return ReasoningOutput(
            action=verdict.action,
            direction=verdict.direction,
            confidence=verdict.confidence,
            risk_level=verdict.risk_level,
            reason_codes=list(verdict.reason_codes),
            insufficient_data=verdict.insufficient_data,
            rationale=verdict.rationale,
            stop_loss=verdict.stop_loss,
            take_profit=verdict.take_profit,
            model=str(response.model),
            prompt_version=PROMPT_VERSION,
            evidence_fingerprint=evidence_fingerprint(evidence),
        )
