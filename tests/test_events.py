"""
Tests for event publishing.
"""


from genmo_fb_common.events import EventPayload, publish_event


class TestEventPayload:
    """Tests for EventPayload dataclass."""

    def test_creates_with_defaults(self):
        """Test payload creates with auto-generated fields."""
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={"key": "value"},
        )

        assert payload.event_type == "test.event"
        assert payload.source_service == "test-service"
        assert payload.data == {"key": "value"}
        assert payload.event_id is not None
        assert payload.timestamp is not None
        assert payload.version == "1.0"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={"key": "value"},
        )

        result = payload.to_dict()

        assert isinstance(result, dict)
        assert result["event_type"] == "test.event"
        assert result["source_service"] == "test-service"
        assert result["data"] == {"key": "value"}

    def test_to_json(self):
        """Test conversion to JSON string."""
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={"key": "value"},
        )

        result = payload.to_json()

        assert isinstance(result, str)
        assert "test.event" in result


class TestPublishEvent:
    """Tests for publish_event function."""

    def test_publishes_successfully(self, mock_rabbitmq):
        """Test successful event publishing."""
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={"key": "value"},
        )

        result = publish_event(payload)

        assert result is True
        mock_rabbitmq.basic_publish.assert_called_once()

    def test_handles_connection_error(self, mocker):
        """Test handling of connection errors."""
        mocker.patch(
            "genmo_fb_common.events.get_rabbitmq_connection",
            side_effect=Exception("Connection failed"),
        )

        payload = EventPayload(
            event_type="test.event", source_service="test-service", data={}
        )

        result = publish_event(payload)

        assert result is False
