"""
Pytest configuration and fixtures.
"""

import json

import pytest

from genmo_fb_common import schemas


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


@pytest.fixture
def widgets(tmp_path, monkeypatch):
    """One small schema, `widget.made` from widget-service, in place of the real ones."""
    schema = {
        "event_type": "widget.made",
        "version": 1,
        "source_service": "widget-service",
        "description": "A widget was made.",
        "data": {
            "type": "object",
            "required": ["widget_id"],
            "properties": {"widget_id": {"type": "string"}},
            "additionalProperties": False,
        },
        "examples": [{"widget_id": "w-1"}],
    }
    (tmp_path / "widget.made.json").write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(schemas, "SCHEMA_DIR", tmp_path)
    schemas._load.cache_clear()
    schemas._validator.cache_clear()
    yield
    schemas._load.cache_clear()
    schemas._validator.cache_clear()
