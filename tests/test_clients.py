"""
Tests for ServiceClient.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.test import RequestFactory

from genmo_fb_common.clients import ServiceClient
from genmo_fb_common.exceptions import ServiceUnavailableError
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


def _answer(status, body=None):
    request = httpx.Request("POST", "http://other-service/api/")
    return httpx.Response(status, json=body or {"ok": True}, request=request)


class _Client(ServiceClient):
    base_url = "http://other-service"
    max_retries = 3
    retry_delay = 0


def _calls(client, outcomes, method, *args, **kwargs):
    """Call `client.<method>` while the other service answers `outcomes` in turn."""
    with patch.object(client._client, "request", side_effect=outcomes) as request:
        with patch("genmo_fb_common.clients.time.sleep"):
            try:
                result = getattr(client, method)(*args, **kwargs)
            except Exception as exc:  # the test looks at which one
                result = exc
    return result, request


class TestRetriesOnlyWhenAskingAgainIsSafe:
    """A write the other service may already have done is never repeated by this client."""

    def test_a_read_is_retried_after_a_5xx_and_a_timeout(self):
        outcomes = [_answer(503), httpx.ReadTimeout("slow"), _answer(200)]
        result, request = _calls(_Client(), outcomes, "get", "/x/")
        assert result == {"ok": True} and request.call_count == 3

    def test_a_read_that_always_fails_is_tried_max_retries_times(self):
        outcomes = [_answer(500)] * 3
        result, request = _calls(_Client(), outcomes, "get", "/x/")
        assert isinstance(result, ServiceUnavailableError)
        assert request.call_count == 3

    @pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
    def test_a_4xx_is_never_retried(self, method):
        args = ("/x/",) if method in ("get", "delete") else ("/x/", {"a": 1})
        result, request = _calls(_Client(), [_answer(404)] * 3, method, *args)
        assert isinstance(result, httpx.HTTPStatusError)
        assert request.call_count == 1

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_a_write_is_not_repeated_after_a_5xx(self, method):
        args = ("/x/",) if method == "delete" else ("/x/", {"a": 1})
        result, request = _calls(_Client(), [_answer(502), _answer(200)], method, *args)
        assert isinstance(result, ServiceUnavailableError)
        assert request.call_count == 1

    @pytest.mark.parametrize("timeout", [httpx.ReadTimeout, httpx.WriteTimeout])
    def test_a_write_is_not_repeated_after_a_timeout(self, timeout):
        outcomes = [timeout("slow"), _answer(200)]
        result, request = _calls(_Client(), outcomes, "post", "/x/", {"a": 1})
        assert isinstance(result, ServiceUnavailableError)
        assert request.call_count == 1

    @pytest.mark.parametrize("never_sent", [httpx.ConnectError, httpx.ConnectTimeout])
    def test_a_write_is_retried_when_it_never_left(self, never_sent):
        outcomes = [never_sent("refused"), never_sent("refused"), _answer(201)]
        result, request = _calls(_Client(), outcomes, "post", "/x/", {"a": 1})
        assert result == {"ok": True} and request.call_count == 3

    def test_a_write_with_an_idempotency_key_is_retried_like_a_read(self):
        outcomes = [httpx.ReadTimeout("slow"), _answer(503), _answer(201)]
        result, request = _calls(
            _Client(),
            outcomes,
            "post",
            "/x/",
            {"a": 1},
            idempotency_key="key-123456789",
        )
        assert result == {"ok": True} and request.call_count == 3
        for call in request.call_args_list:
            assert call.kwargs["headers"]["Idempotency-Key"] == "key-123456789"

    def test_no_idempotency_key_header_is_sent_without_a_key(self):
        _, request = _calls(_Client(), [_answer(201)], "post", "/x/", {"a": 1})
        assert "Idempotency-Key" not in request.call_args.kwargs["headers"]
