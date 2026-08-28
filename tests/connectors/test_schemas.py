"""The connector contract's shape: what it accepts, and what it refuses.

Every assertion here is about a property Mission 11.1 committed to, not about
today's implementation being tidy. The two that matter most are structural:
`Capability` cannot express write or execute, and a `ConnectorResult` cannot
exist without provenance. Both are enforced by the type rather than by a rule
someone has to remember.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from orion.connectors import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    Capability,
    ConnectorResult,
    ConnectorSource,
    ConnectorSpec,
)

from .conftest import HOSTILE_NAME_PARAMS

NOW = datetime(2026, 8, 28, 5, 0, tzinfo=UTC)


# ------------------------------------------------------- read-only capability


class TestCapabilityIsStructurallyReadOnly:
    def test_exactly_one_member_exists(self) -> None:
        # A second member would mean this build could express a connector
        # that changes something outside ORION. Adding one must be a
        # deliberate edit that fails this test first.
        assert list(Capability) == [Capability.READ]
        assert Capability.READ.value == "read"

    def test_write_and_execute_are_not_members(self) -> None:
        for forbidden in ("WRITE", "EXECUTE", "ADMIN", "READ_WRITE", "ACT"):
            assert not hasattr(Capability, forbidden)

    def test_no_write_capability_can_be_constructed_from_a_string(self) -> None:
        # Unrepresentable, not merely rejected by policy: there is no value
        # of this enum that means "may write".
        for forbidden in ("write", "execute", "admin", "read_write"):
            with pytest.raises(ValueError):
                Capability(forbidden)

    def test_a_spec_cannot_declare_anything_but_read(self, spec: ConnectorSpec) -> None:
        assert spec.capability is Capability.READ
        with pytest.raises(ValidationError):
            ConnectorSpec(name="x_demo", capability="write", description="nope")


# -------------------------------------------------------------- connector spec


class TestConnectorSpecIsBounded:
    def test_a_valid_spec_round_trips(self, spec: ConnectorSpec) -> None:
        assert spec.name == "example_read_only"
        assert spec.capability is Capability.READ

    def test_it_is_frozen_and_forbids_unknown_fields(self, spec: ConnectorSpec) -> None:
        with pytest.raises(ValidationError):
            spec.name = "other"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ConnectorSpec(
                name="x_demo",
                capability=Capability.READ,
                description="d",
                base_url="http://attacker.example",  # type: ignore[call-arg]
            )

    def test_there_is_nowhere_to_put_a_url_a_token_or_a_credential(self) -> None:
        # The strongest form of "no caller-provided URL/credential": the
        # fields do not exist, so `extra="forbid"` refuses them by name.
        fields = set(ConnectorSpec.model_fields)
        assert fields == {"name", "capability", "description"}
        for smuggled in ("url", "base_url", "endpoint", "host", "token", "api_key", "headers"):
            assert smuggled not in fields

    @pytest.mark.parametrize("name", HOSTILE_NAME_PARAMS)
    def test_it_refuses_every_hostile_name(self, name: str) -> None:
        with pytest.raises(ValidationError):
            ConnectorSpec(name=name, capability=Capability.READ, description="d")

    def test_it_bounds_the_name_length(self) -> None:
        ConnectorSpec(name="a" * MAX_NAME_CHARS, capability=Capability.READ, description="d")
        with pytest.raises(ValidationError):
            ConnectorSpec(
                name="a" * (MAX_NAME_CHARS + 1), capability=Capability.READ, description="d"
            )

    def test_it_bounds_the_description(self) -> None:
        ConnectorSpec(
            name="x_demo", capability=Capability.READ, description="d" * MAX_DESCRIPTION_CHARS
        )
        for bad in ("", "d" * (MAX_DESCRIPTION_CHARS + 1)):
            with pytest.raises(ValidationError):
                ConnectorSpec(name="x_demo", capability=Capability.READ, description=bad)


# ------------------------------------------------------------------ provenance


class TestProvenanceIsRequired:
    def test_a_source_names_its_connector_and_when_it_was_retrieved(self) -> None:
        source = ConnectorSource(connector="example_read_only", retrieved_at=NOW)
        assert source.connector == "example_read_only"
        assert source.retrieved_at == NOW

    def test_both_fields_are_mandatory(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorSource(connector="example_read_only")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            ConnectorSource(retrieved_at=NOW)  # type: ignore[call-arg]

    def test_a_naive_timestamp_is_refused(self) -> None:
        # A timestamp without a zone cannot be compared to a bar's `as_of`
        # without guessing, and guessing is what this layer refuses.
        with pytest.raises(ValidationError):
            ConnectorSource(connector="example_read_only", retrieved_at=datetime(2026, 8, 28, 5))

    def test_a_non_utc_offset_is_kept_as_sent(self) -> None:
        stamped = datetime(2026, 8, 28, 5, 0, tzinfo=timezone(timedelta(hours=2)))
        source = ConnectorSource(connector="example_read_only", retrieved_at=stamped)
        assert source.retrieved_at.utcoffset() == timedelta(hours=2)

    @pytest.mark.parametrize("name", HOSTILE_NAME_PARAMS)
    def test_a_source_refuses_every_hostile_connector_name(self, name: str) -> None:
        with pytest.raises(ValidationError):
            ConnectorSource(connector=name, retrieved_at=NOW)

    def test_a_source_is_frozen_and_forbids_extra_fields(self) -> None:
        source = ConnectorSource(connector="example_read_only", retrieved_at=NOW)
        with pytest.raises(ValidationError):
            source.connector = "other"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ConnectorSource(
                connector="example_read_only",
                retrieved_at=NOW,
                url="http://attacker.example",  # type: ignore[call-arg]
            )


class TestConnectorResultCannotExistWithoutProvenance:
    def test_a_result_carries_its_source(self) -> None:
        result = ConnectorResult[str](
            source=ConnectorSource(connector="example_read_only", retrieved_at=NOW),
            payload="value",
        )
        assert result.source.connector == "example_read_only"
        assert result.payload == "value"

    def test_a_payload_without_a_source_is_unconstructible(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorResult[str](payload="value")  # type: ignore[call-arg]

    def test_a_source_cannot_be_none(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorResult[str](source=None, payload="value")

    def test_it_is_frozen_and_forbids_extra_fields(self) -> None:
        result = ConnectorResult[str](
            source=ConnectorSource(connector="example_read_only", retrieved_at=NOW),
            payload="value",
        )
        with pytest.raises(ValidationError):
            result.payload = "other"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ConnectorResult[str](
                source=ConnectorSource(connector="example_read_only", retrieved_at=NOW),
                payload="value",
                raw="<script>",  # type: ignore[call-arg]
            )

    def test_hostile_payload_content_is_carried_as_data_not_interpreted(self) -> None:
        # The contract does not sanitise a payload and does not pretend to.
        # It is inert data here; whatever eventually renders it is
        # responsible for treating it as text, exactly as the Phase 5.4
        # analysis deck does.
        hostile = "<script>alert(1)</script>"
        result = ConnectorResult[str](
            source=ConnectorSource(connector="example_read_only", retrieved_at=NOW),
            payload=hostile,
        )
        assert result.payload == hostile
