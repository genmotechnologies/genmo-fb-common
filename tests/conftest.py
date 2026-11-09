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


@pytest.fixture
def family_service():
    """FC-06: family-service faked at httpx.get (tests/family_fake.py); fresh cache each test."""
    from unittest.mock import patch

    from django.core.cache import cache

    from tests.family_fake import answer_for

    cache.clear()
    with patch(
        "genmo_fb_common.ownership.httpx.get",
        side_effect=lambda url, headers, timeout: answer_for(
            headers["Authorization"][7:]
        ),
    ) as fake:
        yield fake
    cache.clear()
