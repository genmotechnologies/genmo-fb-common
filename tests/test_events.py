"""
Tests for event publishing.
"""

import pytest
from unittest.mock import MagicMock, patch

from genmo_fb_common.events import EventPayload, publish_event


class TestEventPayload:
    """Tests for EventPayload dataclass."""
    
    def test_creates_with_defaults(self):
        """Test payload creates with auto-generated fields."""
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={"key": "value"}
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
            data={"key": "value"}
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
            data={"key": "value"}
        )
        
        result = payload.to_json()
        
        assert isinstance(result, str)
        assert "test.event" in result


class TestPublishEvent:
    """Tests for publish_event function."""
    
    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_publishes_successfully(self, mock_get_conn):
        """Test successful event publishing."""
        # Setup mock
        mock_channel = MagicMock()
        mock_connection = MagicMock()
        mock_connection.channel.return_value = mock_channel
        mock_get_conn.return_value = mock_connection
        
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={"key": "value"}
        )
        
        result = publish_event(payload)
        
        assert result is True
        mock_channel.basic_publish.assert_called_once()
    
    @patch("genmo_fb_common.events.get_rabbitmq_connection")
    def test_handles_connection_error(self, mock_get_conn):
        """Test handling of connection errors."""
        mock_get_conn.side_effect = Exception("Connection failed")
        
        payload = EventPayload(
            event_type="test.event",
            source_service="test-service",
            data={}
        )
        
        result = publish_event(payload)
        
        assert result is False