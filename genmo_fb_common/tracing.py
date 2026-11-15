"""
Tracing (FC-08): one trace ID follows a request across services.

Follows the W3C Trace Context header, `traceparent: 00-<trace id>-<span id>-<flags>`. A
service that receives one keeps its trace ID; one that does not starts a new trace. The
trace and span IDs are bound to the logs (so every line of one call, in every service, can
be found by one ID) and `ServiceClient` forwards the header to the next service, with a new
span ID for each call. No tracing backend is needed: the IDs are in the logs, and a
collector can be added later without changing a service.

Add to MIDDLEWARE after RequestIDMiddleware:
    'genmo_fb_common.tracing.TraceContextMiddleware',
"""

import re
import secrets
from contextvars import ContextVar
from typing import Callable, Optional, Tuple

import structlog

TRACEPARENT = "traceparent"

_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-(?P<span>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)

_trace_id: ContextVar[Optional[str]] = ContextVar("gfc_trace_id", default=None)
_flags: ContextVar[str] = ContextVar("gfc_trace_flags", default="01")


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def parse_traceparent(value: Optional[str]) -> Optional[Tuple[str, str, str]]:
    """
    (trace id, parent span id, flags) from a header value, or None when it is missing or
    not valid: only version 00, lowercase hex, and neither ID all zeros, as the standard
    says. A bad header is ignored and a new trace starts; it is never an error.
    """
    if not value:
        return None
    match = _PATTERN.match(value.strip())
    if not match or match["version"] != "00":
        return None
    if set(match["trace"]) == {"0"} or set(match["span"]) == {"0"}:
        return None
    return match["trace"], match["span"], match["flags"]


def get_current_trace_id() -> Optional[str]:
    """The trace ID of the request being served, or None outside one."""
    return _trace_id.get()


def outgoing_traceparent() -> Optional[str]:
    """
    The header to send to the next service: the current trace and a new span ID for this
    call. None outside a request, so a call made by a job simply starts its own trace at
    the other end.
    """
    trace_id = _trace_id.get()
    if not trace_id:
        return None
    return f"00-{trace_id}-{new_span_id()}-{_flags.get()}"


class TraceContextMiddleware:
    """
    Reads `traceparent` (or starts a trace), binds `trace_id` and `span_id` to the logs and
    puts the trace ID on the response as `X-Trace-Id`, so a caller can quote it.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request):
        parsed = parse_traceparent(request.headers.get(TRACEPARENT))
        if parsed:
            trace_id, _parent_span, flags = parsed
        else:
            trace_id, flags = new_trace_id(), "01"
        span_id = new_span_id()

        token_trace = _trace_id.set(trace_id)
        token_flags = _flags.set(flags)
        request.trace_id = trace_id
        structlog.contextvars.bind_contextvars(trace_id=trace_id, span_id=span_id)
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.unbind_contextvars("trace_id", "span_id")
            _trace_id.reset(token_trace)
            _flags.reset(token_flags)

        response["X-Trace-Id"] = trace_id
        return response
