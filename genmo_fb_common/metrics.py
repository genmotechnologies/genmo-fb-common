"""
Metrics (FC-08): request counts and durations in the Prometheus text format, with no
dependency.

    MIDDLEWARE = [..., 'genmo_fb_common.metrics.MetricsMiddleware']
    urlpatterns = [..., path('metrics/', metrics_view)]

`http_requests_total{method,route,status}` and `http_request_duration_seconds{method,route}`
(a histogram). The route label is the URL pattern (`api/v1/goals/<uuid:pk>/`), never the
path that was asked for, so ids do not make a label per goal. Set METRICS_TOKEN in the
settings to make the endpoint need `Authorization: Bearer <token>`; without it the
endpoint is open and should be reachable only from inside the network.

The numbers are per process: with several workers, scrape each, or sum them.
"""

import secrets
import threading
import time
from collections import defaultdict
from typing import Callable, Dict, Iterable, Tuple

from django.conf import settings
from django.http import HttpResponse

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
METRICS_PATH = "metrics"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: Tuple[str, ...], values: Tuple[str, ...], extra: str = "") -> str:
    pairs = [f'{n}="{_escape(v)}"' for n, v in zip(names, values)]
    if extra:
        pairs.append(extra)
    return "{" + ",".join(pairs) + "}" if pairs else ""


class Counter:
    def __init__(self, name: str, help_text: str, label_names: Tuple[str, ...] = ()):
        self.name, self.help, self.label_names = name, help_text, label_names
        self._values: Dict[Tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, *label_values: str, amount: float = 1.0) -> None:
        with self._lock:
            self._values[tuple(label_values)] += amount

    def value(self, *label_values: str) -> float:
        with self._lock:
            return self._values.get(tuple(label_values), 0.0)

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} counter"
        with self._lock:
            for values, number in sorted(self._values.items()):
                yield f"{self.name}{_labels(self.label_names, values)} {number:g}"


class Histogram:
    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Tuple[str, ...] = (),
        buckets: Tuple[float, ...] = DEFAULT_BUCKETS,
    ):
        self.name, self.help, self.label_names = name, help_text, label_names
        self.buckets = tuple(sorted(buckets))
        self._counts: Dict[Tuple[str, ...], list] = {}
        self._sums: Dict[Tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def observe(self, seconds: float, *label_values: str) -> None:
        key = tuple(label_values)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            for index, bound in enumerate(self.buckets):
                if seconds <= bound:
                    counts[index] += 1
            counts[-1] += 1  # +Inf: every observation
            self._sums[key] += seconds

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} histogram"
        with self._lock:
            for key, counts in sorted(self._counts.items()):
                for bound, count in zip(self.buckets, counts):
                    le = f'le="{bound:g}"'
                    yield (
                        f"{self.name}_bucket"
                        f"{_labels(self.label_names, key, le)} {count}"
                    )
                inf = 'le="+Inf"'
                yield (
                    f"{self.name}_bucket"
                    f"{_labels(self.label_names, key, inf)} {counts[-1]}"
                )
                yield f"{self.name}_sum{_labels(self.label_names, key)} {self._sums[key]:g}"
                yield f"{self.name}_count{_labels(self.label_names, key)} {counts[-1]}"


HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Requests served, by method, URL pattern and status code.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "Time to serve a request, by method and URL pattern.",
    ("method", "route"),
)
REGISTRY = [HTTP_REQUESTS, HTTP_DURATION]


def render() -> str:
    lines = []
    for metric in REGISTRY:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"


def route_of(request) -> str:
    """The URL pattern that served the request: bounded, unlike the path."""
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", None)
    return route or "unmatched"


class MetricsMiddleware:
    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.strip("/") == METRICS_PATH:
            return self.get_response(request)  # scraping is not traffic
        started = time.perf_counter()
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            return response
        finally:
            route = route_of(request)
            HTTP_REQUESTS.inc(request.method, route, str(status))
            HTTP_DURATION.observe(time.perf_counter() - started, request.method, route)


def metrics_view(request):
    token = getattr(settings, "METRICS_TOKEN", None)
    if token and not secrets.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {token}"
    ):
        return HttpResponse("Unauthorized", status=401)
    return HttpResponse(render(), content_type=CONTENT_TYPE)
