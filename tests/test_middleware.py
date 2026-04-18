"""
Tests for middleware.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from genmo_fb_common.middleware import IdentitySessionMiddleware


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure LocMemCache is empty between tests so cached sessions don't leak."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture
def get_response():
    """Identity view — returns a 200 response so we can inspect request attrs."""
    mock = MagicMock()
    mock.return_value = MagicMock(status_code=200)
    return mock


def _bearer_request(factory, token="tok-abc"):
    return factory.get("/", HTTP_AUTHORIZATION=f"Bearer {token}")


class TestIdentitySessionMiddleware:
    """Tests for IdentitySessionMiddleware."""

    @patch("httpx.get")
    def test_valid_session_sets_request_attrs(self, mock_get, factory, get_response):
        """Identity-service returns 200 → attrs populated from response body."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "bank_customer_id": "cust-1",
                    "bank_id": "bank-1",
                    "session_id": "sess-1",
                }
            ),
        )

        middleware = IdentitySessionMiddleware(get_response)
        request = _bearer_request(factory)

        middleware(request)

        assert request.bank_customer_id == "cust-1"
        assert request.bank_id == "bank-1"
        assert request.session_id == "sess-1"
        mock_get.assert_called_once()

    @patch("httpx.get")
    def test_401_response_sets_attrs_to_none(self, mock_get, factory, get_response):
        """Identity-service returns 401 → attrs set to None."""
        mock_get.return_value = MagicMock(status_code=401)

        middleware = IdentitySessionMiddleware(get_response)
        request = _bearer_request(factory)

        middleware(request)

        assert request.bank_customer_id is None
        assert request.bank_id is None
        assert request.session_id is None

    @patch("httpx.get")
    def test_second_request_uses_cache(self, mock_get, factory, get_response):
        """Second call with the same token hits the cache, not identity-service."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "bank_customer_id": "cust-1",
                    "bank_id": "bank-1",
                    "session_id": "sess-1",
                }
            ),
        )

        middleware = IdentitySessionMiddleware(get_response)

        first = _bearer_request(factory)
        middleware(first)

        second = _bearer_request(factory)
        middleware(second)

        assert mock_get.call_count == 1
        assert second.bank_customer_id == "cust-1"
        assert second.bank_id == "bank-1"
        assert second.session_id == "sess-1"

    @patch("httpx.get")
    def test_identity_service_unavailable(self, mock_get, factory, get_response):
        """Network error from identity-service → attrs None, request still proceeds."""
        mock_get.side_effect = Exception("connection refused")

        middleware = IdentitySessionMiddleware(get_response)
        request = _bearer_request(factory)

        response = middleware(request)

        assert request.bank_customer_id is None
        assert request.bank_id is None
        assert request.session_id is None
        get_response.assert_called_once_with(request)
        assert response is get_response.return_value

    def test_no_auth_header_sets_attrs_to_none(self, factory, get_response):
        """Missing Authorization header → attrs None, no HTTP call."""
        middleware = IdentitySessionMiddleware(get_response)
        request = factory.get("/")

        middleware(request)

        assert request.bank_customer_id is None
        assert request.bank_id is None
        assert request.session_id is None
