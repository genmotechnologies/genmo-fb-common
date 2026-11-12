"""
Tests for ServiceClient.
"""

from unittest.mock import MagicMock

from django.test import RequestFactory

from genmo_fb_common.clients import ServiceClient
from genmo_fb_common.middleware import RequestIDMiddleware


class TestRequestIdForwarding:
    """A call to another service carries the request ID of the request being served."""

    def test_forwards_the_request_id_of_the_current_request(self):
        seen = {}

        def view(request):
            seen["headers"] = ServiceClient()._get_headers()
            return MagicMock(status_code=200)

        request = RequestFactory().get("/", HTTP_X_REQUEST_ID="req-123")
        RequestIDMiddleware(view)(request)

        assert seen["headers"]["X-Request-ID"] == "req-123"

    def test_forwards_a_generated_request_id(self):
        seen = {}

        def view(request):
            seen["request_id"] = request.request_id
            seen["headers"] = ServiceClient()._get_headers()
            return MagicMock(status_code=200)

        RequestIDMiddleware(view)(RequestFactory().get("/"))

        assert seen["headers"]["X-Request-ID"] == seen["request_id"]

    def test_sends_no_request_id_outside_a_request(self):
        assert "X-Request-ID" not in ServiceClient()._get_headers()

    def test_forwards_the_callers_token(self):
        headers = ServiceClient(auth_token="tok-abc")._get_headers()

        assert headers["Authorization"] == "Bearer tok-abc"
