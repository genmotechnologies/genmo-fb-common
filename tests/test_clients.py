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


class TestErrorAnswersAreNotLogged:
    """PL9: another service's error answer is logged by its code, never its body."""

    SECRET = "4111111111111111"

    def client(self, status, body):
        import httpx

        client = ServiceClient()
        client.base_url = "http://other-service"
        client._client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, **body)
            )
        )
        return client

    def logged(self, call):
        import httpx
        import pytest
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            with pytest.raises(httpx.HTTPStatusError):
                call()
        return [entry for entry in logs if entry["event"] == "service_client_error"]

    def test_logs_the_code_and_not_the_body(self):
        body = {
            "json": {
                "error": {
                    "code": "CARD_NOT_FOUND",
                    "message": f"No card {self.SECRET} for this customer",
                }
            }
        }
        (entry,) = self.logged(lambda: self.client(404, body).get("/cards/1/"))

        assert entry["status"] == 404
        assert entry["error_code"] == "CARD_NOT_FOUND"
        assert "response" not in entry
        assert self.SECRET not in str(entry)

    def test_a_flat_code_is_read_too(self):
        body = {
            "json": {"code": "NOT_YOUR_CHILD", "detail": f"phone +222{self.SECRET}"}
        }
        (entry,) = self.logged(lambda: self.client(403, body).get("/x/"))

        assert entry["error_code"] == "NOT_YOUR_CHILD"
        assert self.SECRET not in str(entry)

    def test_an_answer_that_is_not_json_logs_no_code_and_no_text(self):
        body = {"text": f"<html>card {self.SECRET}</html>"}
        (entry,) = self.logged(lambda: self.client(400, body).get("/x/"))

        assert entry["error_code"] is None
        assert self.SECRET not in str(entry)

    def test_a_code_that_is_not_a_short_token_is_not_logged(self):
        body = {"json": {"error": {"code": f"card {self.SECRET} declined"}}}
        (entry,) = self.logged(lambda: self.client(400, body).get("/x/"))

        assert entry["error_code"] is None
        assert self.SECRET not in str(entry)
