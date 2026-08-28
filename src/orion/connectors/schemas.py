"""The connector contract: what a connector may be, and what it must return.

MISSION 11.1 SHIPS CONTRACTS AND AN EMPTY ALLOWLIST. There is no connector
implementation in this package, no client, no transport, no MCP server, no MCP
client and no tool-calling surface. `CONNECTOR_ALLOWLIST` is empty and every
lookup therefore refuses. That is the deliverable, not a stepping stone left
half-finished: the Mission 11 audit found no candidate connector with
demonstrated value to ORION's analysis chain, so the honest state is a shape
that a future connector must fit and nothing occupying it.

READ IS THE ONLY CAPABILITY THAT EXISTS. `Capability` has one member, for the
same reason `orion.orchestrator.schemas.CapabilityStatus` has one: a
two-member enum would imply a `WRITE` case this build can reach, and a
reviewer would rightly ask where it is honoured. Because `WRITE` and
`EXECUTE` are not *members*, a connector that writes or acts is not
"disallowed by policy" — it is unrepresentable. Granting one later is a
deliberate, reviewable edit to this enum, and it should drag a Decision Log
entry behind it.

THE ALLOWLIST IS CODE, NOT CONFIGURATION. `CONNECTOR_ALLOWLIST` is a module
constant built at import time. It is deliberately *not* read from `Settings`,
an environment variable or a file, so enabling a connector cannot be done by
editing `.env` on a deployed machine — it requires a source change and a
review. This package must never import `orion.config`; a hygiene test proves
it does not.

NO CALLER-PROVIDED URL, NO CALLER-PROVIDED CREDENTIAL. Nothing in these
contracts accepts a URL, a host, a scheme, a header, a token or a key. A
future connector hardcodes its own endpoint the way `orion.voice.deepgram_stt`
already does, rather than taking a base URL the way `TwelveDataProvider` and
`FinnhubEconomicCalendarProvider` do — those two read
`TWELVE_DATA_BASE_URL`/`FINNHUB_BASE_URL` from the environment with no scheme
check and no host allowlist, which is a pattern this layer deliberately does
not inherit.

PROVENANCE IS STRUCTURAL. A `ConnectorResult` cannot be constructed without a
`ConnectorSource` naming the connector and the moment the data was retrieved,
and `retrieved_at` must be timezone-aware. This mirrors
`orion.data.models.DataSourceMeta` and `orion.research.models.
EconomicCalendarSourceMeta` exactly: external data that cannot say where it
came from and when should not be representable, so that "who told us this?"
is answered by the type rather than by a convention someone remembers.

WHY THE REGISTRY LIVES HERE RATHER THAN IN ITS OWN MODULE. Mission 11.1's
approved file list is `__init__.py`, `schemas.py` and `errors.py`. The
allowlist is a validated constant built out of the schema declared directly
above it, so it sits with that schema rather than justifying a fourth file
holding one empty tuple and three small functions.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orion.connectors.errors import ConnectorError

# Bounds. Every one of these is a ceiling on something a *future* connector
# could return, chosen small: these fields identify and describe, they are
# never a payload, and an identifier that needs more than 64 characters is a
# sign something else is being smuggled through it.
MAX_NAME_CHARS: Final = 64
MAX_DESCRIPTION_CHARS: Final = 200

#: Connector names are lowercase snake_case ASCII, like every other closed
#: code vocabulary in ORION (`DecisionReasonCode`, `RiskControlName`). Nothing
#: matching this pattern can carry markup, a path traversal, a scheme, a
#: query string or whitespace.
NAME_PATTERN: Final = r"^[a-z][a-z0-9_]*$"


class Capability(StrEnum):
    """What a connector is permitted to do. Deliberately one member.

    `WRITE` and `EXECUTE` are absent, not disabled. Adding either makes a
    connector able to change something outside ORION, which is a trading- and
    safety-relevant decision that must never be reachable by configuration,
    by a model, or by a caller passing a different enum value.
    """

    READ = "read"


class ConnectorSpec(BaseModel):
    """One allowlist entry: a connector this build is permitted to have.

    A spec is a permission, not an implementation — it grants nothing by
    existing, because nothing in this package can execute a connector.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS, pattern=NAME_PATTERN)
    #: Only `Capability.READ` is constructible; the annotation says so, and
    #: the enum makes anything else unrepresentable rather than rejected.
    capability: Capability
    #: A sentence written in this repository at development time, for a human
    #: reading the allowlist. Never composed from a connector's own response.
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_CHARS)


class ConnectorSource(BaseModel):
    """Where a connector result came from, and when it was retrieved.

    Required for audit, and required for a caller to reason about staleness
    without asking a clock what time it is now — the same reason
    `RiskPanel.portfolio_as_of` carries the stored record's own timestamp
    rather than the moment it was read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    connector: str = Field(min_length=1, max_length=MAX_NAME_CHARS, pattern=NAME_PATTERN)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        # Matches `EconomicCalendarSourceMeta._require_timezone`. A naive
        # timestamp is not a timestamp: it cannot be compared to a bar's
        # `as_of` without guessing a zone, and guessing is what this whole
        # layer exists to refuse.
        if value.tzinfo is None:
            raise ValueError(f"{value!r} has no timezone; must be timezone-aware")
        return value


class ConnectorResult[PayloadT](BaseModel):
    """A validated payload that knows where it came from.

    Generic over the payload so each future connector declares its own
    already-validated shape, exactly as `MarketSnapshot` and
    `EconomicCalendarSnapshot` do, rather than this layer inventing a
    lowest-common-denominator "data" dict that every caller would have to
    re-validate. The one thing every connector shares is that it must say
    where its data came from, and that is what lives here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: ConnectorSource
    payload: PayloadT


def build_allowlist(specs: tuple[ConnectorSpec, ...]) -> tuple[ConnectorSpec, ...]:
    """Validate and freeze an allowlist, rejecting duplicate names.

    Raises `ValueError`, not `ConnectorError`, and names the offending entry.
    That is deliberate and is the one place in this package where an error
    carries detail: `specs` is written in this repository by a developer and
    is validated at import time, so the message can only ever contain a
    string someone typed here — it is never reachable by external input and
    never reaches a user. `ConnectorError` is reserved for the boundary a
    caller can actually drive; see `errors.py`.
    """
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"duplicate connector in allowlist: {spec.name!r}")
        seen.add(spec.name)
    return tuple(specs)


#: Every connector this build is permitted to have: none.
#:
#: Built through `build_allowlist` rather than assigned directly, so the
#: shipped empty allowlist goes through the same validation any future
#: non-empty one will.
CONNECTOR_ALLOWLIST: Final[tuple[ConnectorSpec, ...]] = build_allowlist(())


def allowed_connectors() -> tuple[ConnectorSpec, ...]:
    """Every allowlisted connector. Empty in Mission 11.1."""
    return CONNECTOR_ALLOWLIST


def is_allowed(name: str) -> bool:
    """Whether `name` is allowlisted. Total, and false for everything today.

    Takes and returns nothing that could carry detail outward: a caller
    learns yes or no, never why, and never a near-miss suggestion.
    """
    return any(spec.name == name for spec in CONNECTOR_ALLOWLIST)


def require_connector(name: str) -> ConnectorSpec:
    """The spec for `name`, or refuse.

    Fail-closed and detail-free: `name` may have come from outside this
    process, so the raised `ConnectorError` never echoes it. With an empty
    allowlist this refuses for every input, which is the correct behaviour
    for a build that has no connectors — not a placeholder to be relaxed.
    """
    for spec in CONNECTOR_ALLOWLIST:
        if spec.name == name:
            return spec
    raise ConnectorError from None
