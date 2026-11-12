"""
Tests for the event envelope and publishing (FC-01).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from genmo_fb_common import schemas
from genmo_fb_common.events import (
    EventPayload,
    publish_event,
    send_event,
    validate_event,
)
from tests.helpers import widget, widget_with

ISO_Z = schemas.UTC_TIME


class TestEnvelope:
    def test_has_every_envelope_field(self):
        payload = widget()
        body = payload.to_dict()
        assert set(body) == {
            "event_id",
            "event_type",
            "occurred_at",
            "version",
            "source_service",
            "data",
            "timestamp",
        }
        assert body["version"] == 1

    def test_occurred_at_is_utc_with_a_z(self):
        import re

        assert re.match(ISO_Z, widget().occurred_at)

    def test_the_old_timestamp_name_carries_the_same_time_for_one_release(self):
        body = widget().to_dict()
        assert body["timestamp"] == body["occurred_at"]

    def test_each_event_gets_its_own_id(self):
        assert widget().event_id != widget().event_id

    def test_positional_arguments_still_work(self):
        payload = EventPayload("widget.made", "widget-service", {"widget_id": "w-1"})
        assert payload.event_type == "widget.made"

    def test_a_timestamp_given_at_construction_becomes_occurred_at(self):
        payload = EventPayload(
            "widget.made", "widget-service", {}, timestamp="2026-01-02T03:04:05Z"
        )
        assert payload.occurred_at == "2026-01-02T03:04:05Z"

    def test_to_json_round_trips(self):
        payload = widget()
        assert EventPayload.from_dict(json.loads(payload.to_json())) == payload


class TestReadingTheOldEnvelope:
    OLD = {
        "event_id": "6f1b6c64-6d38-4f9e-8e60-0f1f1b0e8f11",
        "event_type": "widget.made",
        "source_service": "widget-service",
        "data": {"widget_id": "w-1"},
        "timestamp": "2026-10-22T09:30:00.000000Z",
        "version": "1.0",
    }

    def test_timestamp_is_read_as_occurred_at_and_the_version_as_a_number(self):
        payload = EventPayload.from_dict(self.OLD)
        assert payload.occurred_at == "2026-10-22T09:30:00.000000Z"
        assert payload.version == 1

    def test_the_new_name_wins_when_both_are_there(self):
        payload = EventPayload.from_dict(
            {**self.OLD, "occurred_at": "2027-01-01T00:00:00Z"}
        )
        assert payload.occurred_at == "2027-01-01T00:00:00Z"


class TestSchemaEnforcement:
    def test_a_matching_event_passes(self, widgets):
        validate_event(widget())

    @pytest.mark.parametrize(
        "data, problem",
        [
            ({}, "widget_id"),
            ({"widget_id": 5}, "widget_id"),
            ({"widget_id": "w", "extra": 1}, "extra"),
        ],
    )
    def test_a_mismatched_event_raises_in_error_mode(self, widgets, data, problem):
        with pytest.raises(schemas.EventSchemaError, match=problem):
            validate_event(widget(**data) if data else widget_with({}))

    def test_an_event_type_with_no_schema_is_a_mismatch(self, widgets):
        payload = EventPayload("nobody.knows", "widget-service", {})
        with pytest.raises(schemas.EventSchemaError, match="No schema"):
            validate_event(payload)

    def test_the_wrong_publisher_is_a_mismatch(self, widgets):
        payload = EventPayload("widget.made", "other-service", {"widget_id": "w"})
        with pytest.raises(schemas.EventSchemaError, match="published by"):
            validate_event(payload)

    def test_warn_mode_logs_and_lets_it_through(self, widgets, settings):
        settings.GENMO_EVENT_SCHEMA_ENFORCEMENT = "warn"
        validate_event(widget_with({}))

    def test_off_mode_checks_nothing(self, settings):
        settings.GENMO_EVENT_SCHEMA_ENFORCEMENT = "off"
        validate_event(EventPayload("nobody.knows", "x", {}))


class TestPublishEvent:
    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_publishes_with_the_brokers_confirmation(self, mock_get_conn, widgets):
        channel = MagicMock()
        mock_get_conn.return_value.channel.return_value = channel

        assert publish_event(widget()) is True

        channel.confirm_delivery.assert_called_once()
        call = channel.basic_publish.call_args.kwargs
        assert call["routing_key"] == "widget.made"
        assert json.loads(call["body"])["occurred_at"]
        mock_get_conn.return_value.close.assert_called_once()

    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_returns_false_when_the_bus_is_down(self, mock_get_conn, widgets):
        mock_get_conn.side_effect = Exception("Connection failed")
        assert publish_event(widget()) is False

    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_returns_false_when_the_broker_does_not_confirm(
        self, mock_get_conn, widgets
    ):
        channel = MagicMock()
        channel.basic_publish.side_effect = Exception("nacked")
        mock_get_conn.return_value.channel.return_value = channel
        assert publish_event(widget()) is False
        mock_get_conn.return_value.close.assert_called_once()

    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_a_schema_mismatch_is_not_swallowed_as_a_bus_failure(
        self, mock_get_conn, widgets
    ):
        with pytest.raises(schemas.EventSchemaError):
            publish_event(widget_with({}))
        mock_get_conn.assert_not_called()

    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_send_event_raises_instead_of_returning_false(self, mock_get_conn, widgets):
        mock_get_conn.side_effect = Exception("down")
        with pytest.raises(Exception, match="down"):
            send_event(widget())
