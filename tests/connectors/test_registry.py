"""The allowlist: empty, code-defined, and refusing everything.

Mission 11.1's central claim is that ORION has no connectors — not "none
configured", but none permitted, with the permission mechanism itself
shipped and proven. These tests are that proof. The empty case is the
interesting case: `require_connector` must refuse *every* input, including
plausible ones, and must do so without saying why.
"""

from __future__ import annotations

import inspect

import pytest

from orion.connectors import (
    CONNECTOR_ALLOWLIST,
    Capability,
    ConnectorError,
    ConnectorSpec,
    allowed_connectors,
    build_allowlist,
    is_allowed,
    require_connector,
)

from .conftest import HOSTILE_NAME_PARAMS

#: Names a reader might plausibly expect to work, drawn from the brief's own
#: list of candidate connectors. Every one must be refused: the brief lists
#: them as *possibilities*, and none was approved.
PLAUSIBLE_NAMES: tuple[str, ...] = (
    "github",
    "browser",
    "research",
    "documentation",
    "database",
    "notifications",
    "filesystem",
    "shell",
    "broker",
    "example_read_only",
)


class TestTheAllowlistIsEmpty:
    def test_it_ships_with_no_connectors(self) -> None:
        assert CONNECTOR_ALLOWLIST == ()
        assert len(CONNECTOR_ALLOWLIST) == 0

    def test_the_accessor_agrees(self) -> None:
        assert allowed_connectors() == ()

    def test_it_is_an_immutable_tuple_not_a_mutable_list(self) -> None:
        # A list would let any caller append a connector at runtime, which
        # would make the allowlist advisory rather than binding.
        assert isinstance(CONNECTOR_ALLOWLIST, tuple)
        assert isinstance(allowed_connectors(), tuple)

    def test_the_accessor_cannot_be_used_to_mutate_the_allowlist(self) -> None:
        with pytest.raises(AttributeError):
            allowed_connectors().append(  # type: ignore[attr-defined]
                ConnectorSpec(name="x", capability=Capability.READ, description="d")
            )
        assert CONNECTOR_ALLOWLIST == ()

    def test_it_was_built_through_the_same_validator_a_real_one_would_use(self) -> None:
        assert build_allowlist(()) == CONNECTOR_ALLOWLIST


class TestUnknownConnectorsAreRefused:
    @pytest.mark.parametrize("name", PLAUSIBLE_NAMES)
    def test_no_plausible_name_is_allowed(self, name: str) -> None:
        assert is_allowed(name) is False

    @pytest.mark.parametrize("name", PLAUSIBLE_NAMES)
    def test_no_plausible_name_resolves(self, name: str) -> None:
        with pytest.raises(ConnectorError):
            require_connector(name)

    @pytest.mark.parametrize("name", HOSTILE_NAME_PARAMS)
    def test_no_hostile_name_is_allowed(self, name: str) -> None:
        assert is_allowed(name) is False

    @pytest.mark.parametrize("name", HOSTILE_NAME_PARAMS)
    def test_no_hostile_name_resolves(self, name: str) -> None:
        with pytest.raises(ConnectorError):
            require_connector(name)

    def test_a_constructible_spec_is_still_not_a_permitted_one(self, spec: ConnectorSpec) -> None:
        # Being a valid shape grants nothing. This is the distinction the
        # whole layer rests on: the allowlist permits, the schema describes.
        assert is_allowed(spec.name) is False
        with pytest.raises(ConnectorError):
            require_connector(spec.name)


class TestDuplicatesAreRejected:
    def test_two_entries_with_the_same_name_are_refused(self, spec: ConnectorSpec) -> None:
        with pytest.raises(ValueError, match="duplicate connector"):
            build_allowlist((spec, spec))

    def test_a_duplicate_is_caught_even_with_a_different_description(
        self, spec: ConnectorSpec
    ) -> None:
        twin = ConnectorSpec(
            name=spec.name, capability=Capability.READ, description="a different sentence"
        )
        with pytest.raises(ValueError, match="duplicate connector"):
            build_allowlist((spec, twin))

    def test_distinct_names_are_accepted_and_ordered(self, spec: ConnectorSpec) -> None:
        other = ConnectorSpec(name="another_read_only", capability=Capability.READ, description="d")
        built = build_allowlist((spec, other))
        assert [entry.name for entry in built] == [spec.name, other.name]
        assert isinstance(built, tuple)

    def test_building_a_hypothetical_allowlist_does_not_touch_the_real_one(
        self, spec: ConnectorSpec
    ) -> None:
        build_allowlist((spec,))
        assert CONNECTOR_ALLOWLIST == ()
        assert is_allowed(spec.name) is False

    def test_the_duplicate_error_is_not_a_connector_error(self, spec: ConnectorSpec) -> None:
        # Deliberate asymmetry, documented in `build_allowlist`: a duplicate
        # is a developer mistake in this repository's own source, caught at
        # import time and never reachable by external input, so naming it is
        # safe and useful. `ConnectorError` is reserved for the boundary a
        # caller can actually drive.
        with pytest.raises(ValueError) as caught:
            build_allowlist((spec, spec))
        assert not isinstance(caught.value, ConnectorError)


class TestErrorsAreDetailFree:
    def test_the_error_takes_no_constructor_argument(self) -> None:
        # There is no argument position in which a caller could place a
        # connector name, a URL, a query string or a vendor's error text.
        parameters = list(inspect.signature(ConnectorError).parameters)
        assert parameters == []

    def test_the_message_is_fixed(self) -> None:
        assert str(ConnectorError()) == "connector unavailable"

    @pytest.mark.parametrize("name", ("github", "sk-live-secret-key", "http://attacker.example"))
    def test_a_refusal_echoes_nothing_from_the_request(self, name: str) -> None:
        with pytest.raises(ConnectorError) as caught:
            require_connector(name)

        rendered = f"{type(caught.value).__name__}: {caught.value}"
        assert rendered == "ConnectorError: connector unavailable"
        assert name not in rendered

    def test_a_refusal_chains_no_cause_a_traceback_could_print(self) -> None:
        # `raise ... from None`, for the reason `orion.http_errors.
        # raise_detached` documents: a chained cause stays reachable through
        # the traceback that most logging setups print in full.
        with pytest.raises(ConnectorError) as caught:
            require_connector("github")

        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert caught.value.__suppress_context__ is True

    def test_it_fails_closed_rather_than_returning_a_sentinel(self) -> None:
        # No `None`, no empty spec, no "disabled" placeholder a caller could
        # mistake for a usable connector.
        with pytest.raises(ConnectorError):
            require_connector("github")
