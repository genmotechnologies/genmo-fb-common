"""
The outbox (FC-02): an event is written with its change, and sent later, once, even if the
message bus was down when the change happened.
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from genmo_fb_common import events
from genmo_fb_common.outbox.models import OutboxEvent
from genmo_fb_common.outbox.service import publish_pending

pytestmark = pytest.mark.django_db


class Bus:
    """A message bus that can be switched off and records what it was sent."""

    def __init__(self):
        self.up = True
        self.sent: list[events.EventPayload] = []

    def __call__(self, payload):
        if not self.up:
            raise ConnectionError("the bus is down")
        self.sent.append(payload)


@pytest.fixture
def bus():
    return Bus()


def enqueue(widgets, **data):
    return events.enqueue_event(
        "widget.made", data or {"widget_id": "w-1"}, "widget-service"
    )


def test_an_event_is_stored_not_sent(widgets, bus):
    event_id = enqueue(widgets)
    row = OutboxEvent.objects.get()
    assert str(row.event_id) == event_id and row.published_at is None
    assert row.payload["event_type"] == "widget.made"
    assert bus.sent == []


def test_it_is_written_in_the_callers_transaction(widgets):
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            enqueue(widgets)
            raise RuntimeError("the change failed")
    assert OutboxEvent.objects.count() == 0


def test_a_mismatched_event_is_refused_at_enqueue(widgets):
    from genmo_fb_common.schemas import EventSchemaError

    with pytest.raises(EventSchemaError):
        events.enqueue_event("widget.made", {}, "widget-service")
    assert OutboxEvent.objects.count() == 0


def test_the_worker_sends_what_is_due_oldest_first_and_marks_it_sent(widgets, bus):
    for number in range(3):
        enqueue(widgets, widget_id=f"w-{number}")
    result = publish_pending(send=bus)
    assert result == {"sent": 3, "failed": 0, "waiting": 0}
    assert [e.data["widget_id"] for e in bus.sent] == ["w-0", "w-1", "w-2"]
    assert OutboxEvent.objects.filter(published_at__isnull=True).count() == 0
    assert publish_pending(send=bus)["sent"] == 0  # nothing is sent twice


def test_the_sent_event_is_the_one_that_was_stored(widgets, bus):
    event_id = enqueue(widgets)
    publish_pending(send=bus)
    (sent,) = bus.sent
    assert sent.event_id == event_id and sent.source_service == "widget-service"


def test_with_the_bus_down_events_wait_and_go_out_later_exactly_once(widgets, bus):
    """FC-02: stop the bus, make the change, start the bus: delivered once."""
    bus.up = False
    enqueue(widgets, widget_id="w-1")
    first = publish_pending(send=bus)
    assert first == {"sent": 0, "failed": 1, "waiting": 1}
    row = OutboxEvent.objects.get()
    assert row.attempts == 1 and "down" in row.last_error
    assert row.next_attempt_at > timezone.now()

    bus.up = True
    assert publish_pending(send=bus)["sent"] == 0  # not due yet: it waits to retry
    OutboxEvent.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    assert publish_pending(send=bus) == {"sent": 1, "failed": 0, "waiting": 0}
    assert len(bus.sent) == 1


def test_a_failure_ends_the_round_so_a_dead_bus_is_asked_once(widgets, bus):
    bus.up = False
    for number in range(5):
        enqueue(widgets, widget_id=f"w-{number}")
    assert publish_pending(send=bus) == {"sent": 0, "failed": 1, "waiting": 5}
    assert OutboxEvent.objects.filter(attempts=1).count() == 1


def test_retries_back_off_up_to_a_limit(widgets, bus):
    from genmo_fb_common.outbox import service

    bus.up = False
    enqueue(widgets)
    for _ in range(12):
        OutboxEvent.objects.update(next_attempt_at=timezone.now())
        publish_pending(send=bus)
    row = OutboxEvent.objects.get()
    wait = (row.next_attempt_at - timezone.now()).total_seconds()
    assert service.MAX_BACKOFF_SECONDS - 5 <= wait <= service.MAX_BACKOFF_SECONDS


def test_the_batch_size_is_respected(widgets, bus):
    for number in range(5):
        enqueue(widgets, widget_id=f"w-{number}")
    assert publish_pending(limit=2, send=bus) == {"sent": 2, "failed": 0, "waiting": 3}


def test_the_command_runs_one_round(widgets, monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    sent = []
    monkeypatch.setattr(events, "send_event", sent.append)
    enqueue(widgets)
    out = StringIO()
    call_command("publish_outbox", once=True, stdout=out)
    assert len(sent) == 1 and "'sent': 1" in out.getvalue()


def test_publish_event_async_now_goes_through_the_outbox(widgets):
    events.publish_event_async("widget.made", {"widget_id": "w-9"}, "widget-service")
    assert OutboxEvent.objects.get().payload["data"] == {"widget_id": "w-9"}
