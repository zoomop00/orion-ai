"""Turning what the user said into something ORION can act on — the
UNDERSTANDING layer Mission 9 Phase 1 deferred and every mission since has
pointed at.

DETERMINISTIC, NOT A MODEL. This is a closed-vocabulary token matcher: no
LLM, no regex over user-supplied patterns, no network, no clock, no
randomness. Identical text always yields an identical `Intent`. That is a
security decision before it is an engineering one — `transcript.text` is
untrusted external content (`orion.voice.schemas`' module docstring), and
asking a language model "what did they want?" would put that untrusted text
inside a prompt, which is exactly the surface `StaticResponseProvider`
exists to avoid having. A future model-assisted parser is welcome, but it
would need its own fencing and its own approved decision entry; it does not
arrive by accident underneath this function's signature.

NOTHING THE USER TYPED EVER LEAVES THIS MODULE. `Intent.symbol` is not a
substring of the input: it is a value looked up in `_SYMBOL_ALIASES` and
returned from the table. So the string that eventually reaches
`MarketDataProvider.fetch_ohlcv` — and from there a vendor URL — can only
ever be one of the symbols declared here. An unrecognised utterance
resolves to `IntentKind.UNKNOWN` with no symbol at all, never to a
pass-through of whatever was said.

XAUUSD ONLY, ON PURPOSE. `orion.data.twelvedata_provider._SYMBOL_MAP` knows
exactly one symbol, and Missions 0-1 are XAUUSD-only. Listing aliases for
instruments the data layer cannot fetch would produce an `Intent` that is
guaranteed to fail one call later — a worse failure than not recognising
the request, because it looks like it worked.

TIMEFRAME IS NOT PARSED. `DEFAULT_TIMEFRAME` is used for every analyze
intent, and "on the daily" or "four hour" in the utterance does not change
it (there is a test that says so, precisely so nobody assumes otherwise).
Which chart ORION analyses is a policy choice about what ORION *is*, not a
per-utterance option, and making it user-steerable is a product decision
that has not been taken. When it is, it belongs here as an explicit closed
table like the symbols above.
"""

from __future__ import annotations

from orion.data.models import Timeframe
from orion.orchestrator.schemas import Intent, IntentKind

# The chart every analysis runs on. See the module docstring.
DEFAULT_TIMEFRAME = Timeframe.H1

# Longest utterance this will scan. `orion.voice.schemas.Transcript` already
# caps `text` at 2000 characters; restating it means `parse_intent` is safe
# to call on any string, including one that never went through a Transcript
# (a test, or a future caller), without becoming the slow path for a
# pathological input.
MAX_SCANNED_CHARS = 2000

# Verbs that mean "run the analysis". A closed set, matched on whole tokens.
_ANALYZE_TOKENS = frozenset({"analyze", "analyse", "analysis", "analyzing", "analysing"})

# Alias -> the symbol ORION uses internally. Keys are whitespace-normalised
# lowercase token sequences; multi-token keys are matched as contiguous runs
# of tokens, never as substrings, so "golden" can never match "gold".
_SYMBOL_ALIASES: dict[tuple[str, ...], str] = {
    ("gold",): "XAUUSD",
    ("xauusd",): "XAUUSD",
    ("xau", "usd"): "XAUUSD",
}

# Every character that is not a letter or a digit is a separator. Written as
# a translation table rather than a regex so there is no pattern to get
# wrong and no backtracking behaviour to reason about on hostile input.
_KEEP = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _tokenize(text: str) -> tuple[str, ...]:
    """Lowercase `text` and split it into alphanumeric tokens.

    Punctuation, slashes, control characters and every non-alphanumeric byte
    become separators. `"Orion, analyze XAU/USD!"` and `"orion analyze xau
    usd"` therefore produce identical tokens, which is what lets the alias
    table stay small and literal.
    """
    lowered = text[:MAX_SCANNED_CHARS].lower()
    cleaned = "".join(char if char in _KEEP else " " for char in lowered)
    return tuple(cleaned.split())


def _resolve_symbol(tokens: tuple[str, ...]) -> str | None:
    """The first symbol whose alias appears as a contiguous token run.

    Longest aliases are tried first so a two-token alias is never missed
    because one of its tokens also happens to be a one-token alias.
    """
    for alias in sorted(_SYMBOL_ALIASES, key=len, reverse=True):
        width = len(alias)
        for start in range(len(tokens) - width + 1):
            if tokens[start : start + width] == alias:
                return _SYMBOL_ALIASES[alias]
    return None


def parse_intent(text: str) -> Intent:
    """Classify one utterance.

    Total: every string — empty, enormous, hostile, non-English, full of
    control characters — produces an `Intent` and never raises. An utterance
    that does not clearly name both an action this build can perform and a
    symbol it can fetch is `IntentKind.UNKNOWN`, which the caller answers
    with the existing static response rather than with a guess.
    """
    tokens = _tokenize(text)
    if not tokens:
        return Intent(kind=IntentKind.UNKNOWN)

    if not _ANALYZE_TOKENS.intersection(tokens):
        return Intent(kind=IntentKind.UNKNOWN)

    symbol = _resolve_symbol(tokens)
    if symbol is None:
        # ORION was clearly asked to analyse *something*, but nothing it can
        # actually fetch. Refusing here is better than resolving a symbol
        # that is guaranteed to fail at the data provider one call later.
        return Intent(kind=IntentKind.UNKNOWN)

    return Intent(kind=IntentKind.ANALYZE, symbol=symbol, timeframe=DEFAULT_TIMEFRAME)


def known_symbols() -> frozenset[str]:
    """Every symbol this parser can resolve. Exported for tests and for a
    caller that wants to state its own coverage without duplicating the
    table."""
    return frozenset(_SYMBOL_ALIASES.values())
