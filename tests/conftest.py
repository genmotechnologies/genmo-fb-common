"""
Pytest configuration and fixtures.
"""

import pytest


@pytest.fixture
def mock_rabbitmq(mocker):
    """Mock RabbitMQ connection for event tests."""
    mock_conn = mocker.patch("genmo_fb_common.events.get_rabbitmq_connection")
    mock_channel = mocker.MagicMock()
    mock_conn.return_value.channel.return_value = mock_channel
    return mock_channel
