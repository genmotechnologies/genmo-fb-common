"""
FC-08: tracing (one trace ID across services) and metrics (Prometheus text format).
"""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import structlog
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from genmo_fb_common import metrics, tracing
from genmo_fb_common.clients import ServiceClient

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"
VALID = f"00-{TRACE}-{SPAN}-01"


class TestParse:
    def test_a_valid_header_is_read(self):
        assert tracing.parse_traceparent(VALID) == (TRACE, SPAN, "01")

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "garbage",
            f"01-{TRACE}-{SPAN}-01",  # a version this code does not know
            f"00-{TRACE.upper()}-{SPAN}-01",  # the standard says lowercase
            f"00-{'0' * 32}-{SPAN}-01",  # an all-zero trace ID is invalid
            f"00-{TRACE}-{'0' * 16}-01",  # and so is an all-zero span ID
            f"00-{TRACE[:-1]}-{SPAN}-01",  # too short
            f"00-{TRACE}-{SPAN}-01-extra",
        ],
    )
    def test_anything_else_is_ignored_not_an_error(self, value):
        assert tracing.parse_traceparent(value) is None


class TestMiddleware:
    def run(self, header=None):
        seen = {}

        def view(request):
            seen["trace"] = tracing.get_current_trace_id()
            seen["outgoing"] = tracing.outgoing_traceparent()
            seen["request_trace"] = request.trace_id
            seen["context"] = structlog.contextvars.get_contextvars()
            return HttpResponse("ok")

        headers = {"HTTP_TRACEPARENT": header} if header else {}
        response = tracing.TraceContextMiddleware(view)(
            RequestFactory().get("/", **headers)
        )
        return seen, response

    def test_an_incoming_trace_is_kept(self):
        seen, response = self.run(VALID)

        assert seen["trace"] == TRACE == seen["request_trace"]
        assert response["X-Trace-Id"] == TRACE

    def test_without_one_a_trace_is_started(self):
        seen, response = self.run()

        assert re.fullmatch(r"[0-9a-f]{32}", seen["trace"])
        assert response["X-Trace-Id"] == seen["trace"]

    def test_a_bad_header_starts_a_new_trace(self):
        seen, _ = self.run("not-a-header")

        assert re.fullmatch(r"[0-9a-f]{32}", seen["trace"])
        assert seen["trace"] != TRACE

    def test_the_logs_carry_the_trace_and_span(self):
        seen, _ = self.run(VALID)

        assert seen["context"]["trace_id"] == TRACE
        assert re.fullmatch(r"[0-9a-f]{16}", seen["context"]["span_id"])
        assert seen["context"]["span_id"] != SPAN  # this service's own span

    def test_nothing_is_left_behind_after_the_request(self):
        self.run(VALID)

        assert tracing.get_current_trace_id() is None
        assert "trace_id" not in structlog.contextvars.get_contextvars()
        assert tracing.outgoing_traceparent() is None

    def test_the_next_call_gets_the_same_trace_and_a_new_span(self):
        seen, _ = self.run(VALID)

        version, trace, span, flags = seen["outgoing"].split("-")
        assert (version, trace, flags) == ("00", TRACE, "01")
        assert span != SPAN and re.fullmatch(r"[0-9a-f]{16}", span)


class TestClientForwardsTheTrace:
    def test_a_call_made_while_serving_a_request_carries_the_trace(self):
        seen = {}

        def view(request):
            seen["headers"] = ServiceClient()._get_headers()
            return HttpResponse("ok")

        request = RequestFactory().get("/", HTTP_TRACEPARENT=VALID)
        tracing.TraceContextMiddleware(view)(request)

        assert seen["headers"]["traceparent"].split("-")[1] == TRACE

    def test_a_call_made_outside_a_request_carries_none(self):
        assert "traceparent" not in ServiceClient()._get_headers()


@pytest.fixture
def fresh_metrics():
    metrics.HTTP_REQUESTS._values.clear()
    metrics.HTTP_DURATION._counts.clear()
    metrics.HTTP_DURATION._sums.clear()
    yield
    metrics.HTTP_REQUESTS._values.clear()
    metrics.HTTP_DURATION._counts.clear()
    metrics.HTTP_DURATION._sums.clear()


@pytest.mark.usefixtures("fresh_metrics")
class TestMetrics:
    def serve(self, path="/api/v1/goals/1/", route="api/v1/goals/<uuid:pk>/", code=200):
        def view(request):
            request.resolver_match = SimpleNamespace(route=route)
            return MagicMock(status_code=code)

        return metrics.MetricsMiddleware(view)(RequestFactory().get(path))

    def test_a_request_is_counted_by_pattern_and_status(self):
        self.serve()
        self.serve()
        self.serve(code=404)

        route = "api/v1/goals/<uuid:pk>/"
        assert metrics.HTTP_REQUESTS.value("GET", route, "200") == 2
        assert metrics.HTTP_REQUESTS.value("GET", route, "404") == 1

    def test_the_label_is_the_pattern_so_ids_do_not_make_a_label_each(self):
        for goal in ("a", "b", "c"):
            self.serve(path=f"/api/v1/goals/{goal}/")

        text = metrics.render()
        assert text.count("http_requests_total{") == 1
        assert "/goals/a/" not in text

    def test_an_unmatched_path_is_one_label(self):
        def view(request):
            return MagicMock(status_code=404)

        metrics.MetricsMiddleware(view)(RequestFactory().get("/nope/1/"))
        metrics.MetricsMiddleware(view)(RequestFactory().get("/nope/2/"))

        assert metrics.HTTP_REQUESTS.value("GET", "unmatched", "404") == 2

    def test_a_view_that_raises_is_counted_as_a_500_and_the_error_goes_on(self):
        def boom(request):
            raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            metrics.MetricsMiddleware(boom)(RequestFactory().get("/x/"))

        assert metrics.HTTP_REQUESTS.value("GET", "unmatched", "500") == 1

    def test_scraping_is_not_counted(self):
        metrics.MetricsMiddleware(lambda r: MagicMock(status_code=200))(
            RequestFactory().get("/metrics/")
        )

        assert metrics.render().count("http_requests_total{") == 0

    def test_the_histogram_buckets_are_cumulative(self):
        metrics.HTTP_DURATION.observe(0.02, "GET", "r")
        metrics.HTTP_DURATION.observe(0.3, "GET", "r")

        text = metrics.render()
        assert 'http_request_duration_seconds_bucket{method="GET",route="r",le="0.01"} 0' in text
        assert 'le="0.025"} 1' in text
        assert 'le="0.5"} 2' in text
        assert 'le="+Inf"} 2' in text
        assert 'http_request_duration_seconds_count{method="GET",route="r"} 2' in text
        assert 'http_request_duration_seconds_sum{method="GET",route="r"} 0.32' in text

    def test_the_output_has_help_and_type_lines(self):
        text = metrics.render()

        assert "# TYPE http_requests_total counter" in text
        assert "# TYPE http_request_duration_seconds histogram" in text
        assert text.endswith("\n")

    def test_a_label_value_is_escaped(self):
        metrics.HTTP_REQUESTS.inc("GET", 'a"b\\c', "200")

        assert 'route="a\\"b\\\\c"' in metrics.render()

    def test_the_endpoint_serves_the_text_format(self):
        response = metrics.metrics_view(RequestFactory().get("/metrics/"))

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain; version=0.0.4")

    @override_settings(METRICS_TOKEN="s3cret")
    def test_with_a_token_set_the_endpoint_needs_it(self):
        assert metrics.metrics_view(RequestFactory().get("/metrics/")).status_code == 401
        wrong = RequestFactory().get("/metrics/", HTTP_AUTHORIZATION="Bearer nope")
        assert metrics.metrics_view(wrong).status_code == 401
        right = RequestFactory().get("/metrics/", HTTP_AUTHORIZATION="Bearer s3cret")
        assert metrics.metrics_view(right).status_code == 200
