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

# Words that open a conversation. Whole tokens only, so "this" never matches
# "hi" and "hello world" in the middle of a sentence still reads as a greeting
# rather than as nothing at all.
_GREETING_TOKENS = frozenset(
    {"hello", "hi", "hey", "yo", "greetings", "morning", "afternoon", "evening"}
)

# Asking what ORION is or what it can do. One token is enough for the
# unambiguous ones; the phrasings people actually use are matched as
# contiguous token runs, the same mechanism `_SYMBOL_ALIASES` uses.
_CAPABILITY_TOKENS = frozenset({"help", "capabilities", "commands"})

# Phrases are matched as contiguous runs, so a longer sentence that *contains*
# one is already covered and does not need its own entry: "tell me what you
# can do" matches ("what", "can", "you", "do"), and "what are your
# capabilities" matches the `capabilities` token above. Only phrasings that
# share no shorter form with an entry already here are listed.
_CAPABILITY_PHRASES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("what", "can", "you", "do"),
        # The indirect word order, which is what "tell me what you can do"
        # and "explain what you can do" actually contain.
        ("what", "you", "can", "do"),
        ("what", "do", "you", "do"),
        ("what", "can", "i", "ask"),
        ("who", "are", "you"),
        ("what", "are", "you"),
        ("what", "are", "you", "capable", "of"),
        ("what", "is", "orion", "capable", "of"),
        ("what", "can", "orion", "do"),
        ("what", "does", "orion", "do"),
    }
)

# Chat shorthand, folded onto the word it stands for BEFORE any table is
# consulted.
#
# THIS IS WHY "what can u do" WORKS. Every table in this module matches whole
# tokens, so "u" and "you" were two different words and the phrase above
# simply did not match -- a real user asking a supported question got the
# unrecognised answer back. Normalising one token to another keeps that fix
# inside the same closed-vocabulary model rather than loosening the matcher:
# this is a fixed dictionary written at development time, applied per token,
# with no regex, no stemming, no edit distance and no inference. A word that
# is not a key is passed through untouched.
#
# IT CANNOT WIDEN WHAT ORION WILL FETCH. None of these values is a symbol
# alias, so no rewriting here can resolve a symbol that the raw text could
# not, and `_SYMBOL_ALIASES` remains the only route to a `symbol` at all.
_TOKEN_NORMALISATION: dict[str, str] = {
    "u": "you",
    "ur": "your",
    "r": "are",
    "wat": "what",
    "wot": "what",
    "hii": "hi",
    "helo": "hello",
    "hullo": "hello",
    "analyzed": "analyze",
    "analysed": "analyse",
}

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

    Each token is then looked up in `_TOKEN_NORMALISATION`, so a handful of
    fixed chat spellings ("u", "ur", "wat") reach the tables below as the
    words they stand for. The rewrite is a dictionary lookup on a whole
    token and nothing else -- see that table for why it cannot widen what
    ORION is willing to fetch.
    """
    lowered = text[:MAX_SCANNED_CHARS].lower()
    cleaned = "".join(char if char in _KEEP else " " for char in lowered)
    return tuple(_TOKEN_NORMALISATION.get(token, token) for token in cleaned.split())


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


def _contains_run(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Whether `phrase` appears as a contiguous run of whole tokens."""
    width = len(phrase)
    return any(tokens[i : i + width] == phrase for i in range(len(tokens) - width + 1))


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

    # Ordered by cost and specificity, and the order is the contract: a
    # request to analyse outranks a greeting attached to it, so "hi, analyse
    # gold" runs the analysis rather than saying hello back.
    if _ANALYZE_TOKENS.intersection(tokens):
        symbol = _resolve_symbol(tokens)
        if symbol is not None:
            return Intent(kind=IntentKind.ANALYZE, symbol=symbol, timeframe=DEFAULT_TIMEFRAME)
        # ORION was clearly asked to analyse *something*, but nothing it can
        # actually fetch. Falling through is better than resolving a symbol
        # that is guaranteed to fail at the data provider one call later.

    if _CAPABILITY_TOKENS.intersection(tokens) or any(
        _contains_run(tokens, phrase) for phrase in _CAPABILITY_PHRASES
    ):
        return Intent(kind=IntentKind.CAPABILITIES)

    if _GREETING_TOKENS.intersection(tokens):
        return Intent(kind=IntentKind.GREETING)

    return Intent(kind=IntentKind.UNKNOWN)


def known_symbols() -> frozenset[str]:
    """Every symbol this parser can resolve. Exported for tests and for a
    caller that wants to state its own coverage without duplicating the
    table."""
    return frozenset(_SYMBOL_ALIASES.values())
