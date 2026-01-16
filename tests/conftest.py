"""
Pytest configuration and fixtures.
"""

import pytest
from django.conf import settings


def pytest_configure():
    """Configure Django settings for tests."""
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        SECRET_KEY='test-secret-key',
        CELERY_BROKER_URL='memory://',
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    )


@pytest.fixture
def mock_rabbitmq(mocker):
    """Mock RabbitMQ connection for event tests."""
    mock_conn = mocker.patch('genmo_fb_common.events.get_rabbitmq_connection')
    mock_channel = mocker.MagicMock()
    mock_conn.return_value.channel.return_value = mock_channel
    return mock_channel