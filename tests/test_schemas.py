"""
The published event schemas (FC-01, FC-04): every event type has one, each is a valid
schema whose own example passes, and a mismatch is caught.
"""

import json
import re

import pytest
from jsonschema import Draft202012Validator

from genmo_fb_common import schemas
from genmo_fb_common.events import EventPayload

TYPES = schemas.event_types()


def test_there_is_a_schema_for_every_event_the_services_publish():
    published = {
        "transfer": ["initiated", "completed", "failed"],
        "money_request": ["created", "approved", "declined"],
        "family": ["created", "updated", "deleted"],
        "limits": ["rule.changed", "exceeded"],
    }
    for family, names in published.items():
        for name in names:
            assert f"{family}.{name}" in TYPES
    assert len(TYPES) >= 50


@pytest.mark.parametrize("event_type", TYPES)
def test_each_schema_is_valid_and_named_after_its_file(event_type):
    schema = schemas.get_schema(event_type)
    Draft202012Validator.check_schema(schema["data"])
    assert re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", event_type)
    assert schema["source_service"].endswith("-service")
    assert schema["version"] == 1 and schema["description"]


@pytest.mark.parametrize("event_type", TYPES)
def test_each_examples_makes_a_valid_event(event_type):
    event = schemas.example_event(event_type)
    schemas.validate(event)
    assert event["event_type"] == event_type
    assert event["source_service"] == schemas.get_schema(event_type)["source_service"]
    # An event survives its own JSON round trip, as it does on the bus.
    schemas.validate(json.loads(json.dumps(event)))


@pytest.mark.parametrize("event_type", TYPES)
def test_a_missing_required_field_is_caught(event_type):
    schema = schemas.get_schema(event_type)
    for name in schema["data"]["required"]:
        data = {k: v for k, v in schema["examples"][0].items() if k != name}
        with pytest.raises(schemas.EventSchemaError, match=name):
            schemas.validate_data(event_type, data)


@pytest.mark.parametrize("event_type", TYPES)
def test_an_unknown_field_is_caught(event_type):
    data = {**schemas.get_schema(event_type)["examples"][0], "sender_wallet": "x"}
    with pytest.raises(schemas.EventSchemaError, match="sender_wallet"):
        schemas.validate_data(event_type, data)


def test_the_field_names_one_event_uses_are_the_ones_its_consumers_read():
    """FC-04: transfer.completed names the payer from_bank_customer_id, as limits reads it."""
    data = schemas.get_schema("transfer.completed")["data"]["properties"]
    assert "from_bank_customer_id" in data and "to_bank_customer_id" in data
    assert "sender_bank_customer_id" not in data


def test_only_rejected_exists_for_a_chore_sent_back():
    """No consumer reads chore.instance.declined, so it is gone."""
    assert "chore.instance.rejected" in TYPES
    assert "chore.instance.declined" not in TYPES


@pytest.mark.parametrize(
    "event_type",
    [
        "goal.request.created",
        "goal.request.approved",
        "goal.request.rejected",
        "goal.withdrawn",
    ],
)
def test_goal_request_events_name_the_child_owner_bank_customer_id(event_type):
    """The goal's child is its owner, as on every other goal event."""
    data = schemas.get_schema(event_type)["data"]
    assert "owner_bank_customer_id" in data["required"]
    old = data["properties"].get("child_bank_customer_id")
    if old:  # still accepted for one more release, never required
        assert old["deprecated"] and "child_bank_customer_id" not in data["required"]


@pytest.mark.parametrize(
    "amount, ok",
    [("50.00", True), ("50", True), ("-1.5", True), ("1E+2", False), ("abc", False)],
)
def test_amounts_are_decimal_strings_never_numbers_or_exponents(amount, ok):
    data = {**schemas.get_schema("transfer.completed")["examples"][0], "amount": amount}
    if ok:
        schemas.validate_data("transfer.completed", data)
    else:
        with pytest.raises(schemas.EventSchemaError):
            schemas.validate_data("transfer.completed", data)
    data["amount"] = 50.0  # a float is never allowed
    with pytest.raises(schemas.EventSchemaError):
        schemas.validate_data("transfer.completed", data)


def test_bank_event_amounts_have_exactly_two_places():
    data = {**schemas.get_schema("bank.card.authorized")["examples"][0], "amount": "5"}
    with pytest.raises(schemas.EventSchemaError):
        schemas.validate_data("bank.card.authorized", data)


@pytest.mark.parametrize(
    "event, problem",
    [
        ({"event_type": "transfer.completed"}, "envelope is missing"),
        ({"event_type": "nobody.knows"}, "envelope is missing"),
    ],
)
def test_an_incomplete_envelope_is_refused(event, problem):
    with pytest.raises(schemas.EventSchemaError, match=problem):
        schemas.validate(event)


def test_the_wrong_publisher_or_version_is_refused():
    event = schemas.example_event("transfer.completed")
    with pytest.raises(schemas.EventSchemaError, match="published by"):
        schemas.validate({**event, "source_service": "limits-service"})
    with pytest.raises(schemas.EventSchemaError, match="version"):
        schemas.validate({**event, "version": 2})


def test_example_event_lets_a_consumer_test_change_one_field():
    event = schemas.example_event("transfer.completed", amount="75.00")
    assert event["data"]["amount"] == "75.00"
    with pytest.raises(schemas.EventSchemaError):
        schemas.example_event("transfer.completed", amount="not money")


def test_an_event_built_by_the_envelope_class_passes_its_schema():
    payload = EventPayload(
        "limits.exceeded",
        "limits-service",
        schemas.get_schema("limits.exceeded")["examples"][0],
    )
    schemas.validate(payload.to_dict())


def test_what_the_services_really_send_is_allowed():
    """Found by running every service's tests and checking the events they build."""
    schemas.example_event("chore.instance.approved", reward_points=None)
    schemas.example_event("chore.reward.paid", reward_points=None)
    schemas.example_event("goal.created", deadline="2026-11-11")  # a date, not a time
    schemas.example_event("goal.created", deadline="2026-11-11T08:00:00Z")
    schemas.example_event("goal.created", deadline=None)


class TestConsumerContract:
    def test_fields_the_schema_declares_pass(self):
        schemas.check_consumer_reads(
            "transfer.completed",
            ["from_bank_customer_id", "amount", "category"],
            always=["from_bank_customer_id", "amount"],
        )

    def test_a_renamed_field_is_caught(self):
        with pytest.raises(schemas.EventSchemaError, match="sender_bank_customer_id"):
            schemas.check_consumer_reads(
                "transfer.completed", ["sender_bank_customer_id"]
            )

    def test_a_field_the_publisher_may_leave_null_must_not_be_indexed(self):
        with pytest.raises(schemas.EventSchemaError, match="category"):
            schemas.check_consumer_reads(
                "transfer.completed", ["category"], always=["category"]
            )

    def test_a_field_the_publisher_may_omit_must_not_be_indexed(self):
        with pytest.raises(schemas.EventSchemaError, match="child_bank_customer_id"):
            schemas.check_consumer_reads(
                "chore.instance.expired",
                ["child_bank_customer_id"],
                always=["child_bank_customer_id"],
            )

    def test_the_consumers_that_exist_today(self):
        """What limits-service and family-service read now, from their code."""
        schemas.check_consumer_reads(
            "transfer.completed",
            [
                "transfer_type",
                "category",
                "transfer_id",
                "from_bank_customer_id",
                "amount",
                "family_id",
                "currency",
                "completed_at",
            ],
        )
        schemas.check_consumer_reads(
            "bank.card.authorized",
            ["channel", "bank_customer_id", "amount", "currency", "occurred_at"],
            always=["channel", "bank_customer_id", "amount", "currency"],
        )
        schemas.check_consumer_reads(
            "user.session.created", ["bank_customer_id"], always=["bank_customer_id"]
        )

    def test_account_service_reads_fields_no_transfer_event_has(self):
        """account-service reads source_wallet_id and destination_wallet_id: unmatched."""
        with pytest.raises(schemas.EventSchemaError, match="source_wallet_id"):
            schemas.check_consumer_reads(
                "transfer.completed", ["source_wallet_id", "destination_wallet_id"]
            )
