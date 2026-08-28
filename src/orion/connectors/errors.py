"""The one way a connector boundary refuses.

DETAIL-FREE BY CONSTRUCTION, NOT BY CONVENTION. `ConnectorError.__init__`
takes no parameter, so there is no argument position in which a caller could
place a connector name, a URL, a query string, a credential or a vendor's
own error text. That is the same discipline `orion.orchestrator.response`
applies to `ResponseProviderError` and the frontend applies to
`OrionApiError`/`OrionAnalysisError`: a boundary that cannot be handed detail
cannot leak it, which is stronger than a boundary that merely does not.

It matters more here than almost anywhere else in ORION. A connector is, by
definition, a thing that talks to something outside this codebase, and
`orion.http_errors` exists because a real HIGH-severity finding in this
repository was an external provider's exception embedding an API key in its
`str()`. Any future connector implementation must raise *this* error and must
not chain the original — `raise ConnectorError() from None`, matching
`orion.http_errors.raise_detached`'s reasoning, because chaining leaves the
credential-bearing cause reachable through the traceback that most logging
setups print in full.

FAIL CLOSED. There is deliberately no `ConnectorWarning`, no partial result
and no "degraded" outcome. A connector that cannot return validated data
returns nothing and raises; a caller may then report an explicit, honest
absence — the way `RiskAvailability` and `NotConnectedPanel` already report
one — but it may never substitute a default, a cached value or an empty
payload for data that was not retrieved.

ONE CLASS, ON PURPOSE. A hierarchy distinguishing "not allowlisted" from
"unavailable" would be a design decision taken on behalf of connectors that
do not exist yet. When the first one is built and a caller genuinely needs to
branch, a subclass can be added deliberately; adding it now would be
speculative, and every subclass is one more place a future edit could attach
a message parameter.
"""

from __future__ import annotations


class ConnectorError(RuntimeError):
    """A connector boundary refused. Carries no detail, and cannot.

    Never raised with a cause attached (`from None`, never `from exc`): see
    the module docstring, and `orion.http_errors.raise_detached` for the
    finding that established the rule.
    """

    def __init__(self) -> None:
        super().__init__("connector unavailable")
