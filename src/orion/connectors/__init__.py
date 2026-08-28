"""ORION connector contracts — Mission 11.1.

A contract and allowlist layer with **no connector in it**. This package
declares what a connector would have to be, refuses every lookup because the
allowlist is empty, and contains no client, no transport, no MCP server, no
MCP client, no tool-calling surface and no network, filesystem, subprocess or
logging capability of any kind. `tests/connectors/test_import_hygiene.py`
proves that from the source rather than asserting it here.

WHY IT EXISTS BEFORE ANY CONNECTOR DOES. The Mission 11 audit found that no
candidate connector had demonstrated value to ORION's analysis chain, and
that the only MCP implementation actually present in the repository was one
nobody chose — Next.js 16's development MCP server, on by default, which
Mission 11.1 turns off explicitly. Shipping the shape first, empty, is what
makes the *next* decision a small reviewable diff against a stated contract
rather than an argument conducted while a connector is already half-written.

The brief's §19 rules map onto this package one for one: explicit allowlist
(`CONNECTOR_ALLOWLIST`, code not configuration), least privilege and
read-only (`Capability` has a single `READ` member, so write and execute are
unrepresentable), secret isolation (nothing here accepts a credential, a URL
or `Settings`), fail-closed (`ConnectorError` carries no detail and
`require_connector` refuses by default), and never bypass ORION risk controls
— enforced by hygiene, since this package may not import `orion.risk`,
`orion.decision`, `orion.paper` or `orion.memory` and therefore cannot reach
the Decision Engine or the Risk Guardian even by accident.

A connector must also never become a trading authority by the back door. The
intended direction, when a connector is eventually built, is that its output
enters as validated *evidence* upstream of reasoning — the way
`MarketSnapshot` and `EconomicCalendarSnapshot` already do — and never as a
tool a model may call. Nothing in this package provides a mechanism for the
latter, which is the point.
"""

from orion.connectors.errors import ConnectorError
from orion.connectors.schemas import (
    CONNECTOR_ALLOWLIST,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    NAME_PATTERN,
    Capability,
    ConnectorResult,
    ConnectorSource,
    ConnectorSpec,
    allowed_connectors,
    build_allowlist,
    is_allowed,
    require_connector,
)

__all__ = [
    "CONNECTOR_ALLOWLIST",
    "MAX_DESCRIPTION_CHARS",
    "MAX_NAME_CHARS",
    "NAME_PATTERN",
    "Capability",
    "ConnectorError",
    "ConnectorResult",
    "ConnectorSource",
    "ConnectorSpec",
    "allowed_connectors",
    "build_allowlist",
    "is_allowed",
    "require_connector",
]
