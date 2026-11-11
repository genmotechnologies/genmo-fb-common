"""
Idempotent consumers (FC-03): the same event delivered twice acts once.
"""

import json

import pytest

from genmo_fb_common import schemas
from genmo_fb_common.consumers import handle_once, parse_event
from genmo_fb_common.outbox.models import ProcessedEvent
from tests.helpers import widget

pytestmark = pytest.mark.django_db


def test_a_repeat_of_the_same_event_runs_the_handler_once(widgets):
    paid = []
    event = widget()
    assert handle_once("rewards", event, paid.append) is True
    assert handle_once("rewards", event, paid.append) is False
    assert [e.event_id for e in paid] == [event.event_id]


def test_two_consumers_each_handle_the_same_event_once(widgets):
    seen = []
    event = widget()
    handle_once("rewards", event, lambda e: seen.append("rewards"))
    handle_once("badges", event, lambda e: seen.append("badges"))
    handle_once("badges", event, lambda e: seen.append("badges"))
    assert seen == ["rewards", "badges"]


def test_a_failing_handler_records_nothing_so_the_redelivery_is_handled(
    widgets,
):
    event = widget()
    calls = []

    def flaky(e):
        calls.append(e)
        if len(calls) == 1:
            raise RuntimeError("database hiccup")

    with pytest.raises(RuntimeError):
        handle_once("rewards", event, flaky)
    assert ProcessedEvent.objects.count() == 0
    assert handle_once("rewards", event, flaky) is True
    assert len(calls) == 2


def test_the_handlers_own_writes_roll_back_with_its_failure(widgets):
    def writes_then_fails(e):
        ProcessedEvent.objects.create(consumer="side-effect", event_id="x")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        handle_once("rewards", widget(), writes_then_fails)
    assert ProcessedEvent.objects.count() == 0


def test_parse_event_reads_the_current_envelope(widgets):
    event = widget()
    parsed = parse_event(event.to_json().encode())
    assert parsed.event_id == event.event_id and parsed.data == event.data


def test_parse_event_reads_the_old_envelope(widgets):
    old = {
        "event_id": "6f1b6c64-6d38-4f9e-8e60-0f1f1b0e8f11",
        "event_type": "widget.made",
        "source_service": "widget-service",
        "data": {"widget_id": "w-1"},
        "timestamp": "2026-10-22T09:30:00.000000Z",
        "version": "1.0",
    }
    parsed = parse_event(json.dumps(old))
    assert parsed.occurred_at == old["timestamp"] and parsed.version == 1


@pytest.mark.parametrize("body", ["not json", "[]", '{"event_type": "widget.made"}'])
def test_parse_event_refuses_what_is_not_an_event(widgets, body):
    with pytest.raises(schemas.EventSchemaError, match="not an event"):
        parse_event(body)


def test_parse_event_checks_the_schema(widgets):
    bad = widget().to_dict()
    bad["data"] = {"wrong": 1}
    with pytest.raises(schemas.EventSchemaError):
        parse_event(bad)
