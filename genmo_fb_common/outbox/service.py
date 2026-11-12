"""The outbox (FC-02): write an event with the change, send it afterwards, keep trying."""

from datetime import timedelta

import structlog
from django.db import connection, transaction
from django.utils import timezone

from .. import events
from .models import OutboxEvent

logger = structlog.get_logger(__name__)

BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300


def enqueue(payload: "events.EventPayload") -> str:
    """Store the event. Call it inside the transaction of the change it reports."""
    events.validate_event(payload)
    OutboxEvent.objects.create(
        event_id=payload.event_id,
        event_type=payload.event_type,
        payload=payload.to_dict(),
        created_at=timezone.now(),
        next_attempt_at=timezone.now(),
    )
    return payload.event_id


def publish_pending(limit: int = 100, send=None) -> dict:
    """
    Send the events that are due, oldest first. Returns {"sent": n, "failed": n,
    "waiting": n}. The first failure ends the round, so a bus that is down is asked once,
    not once per event, and events keep their order. Rows are locked while they are sent,
    so two workers never send the same one. An event can still be sent twice if a worker
    dies between the broker's confirmation and the commit, which is why consumers record
    the event_ids they have handled (FC-03).

    shortcut: one broker connection per event; move to one per round when volume needs it.
    """
    send = send or events.send_event
    sent = failed = 0
    with transaction.atomic():
        rows = OutboxEvent.objects.select_for_update(
            # SQLite (used by some tests) has neither row locks nor SKIP LOCKED.
            skip_locked=connection.features.has_select_for_update_skip_locked
        )
        due = rows.filter(
            published_at__isnull=True, next_attempt_at__lte=timezone.now()
        ).order_by("id")[:limit]
        for row in due:
            try:
                send(events.EventPayload.from_dict(row.payload))
            except Exception as error:
                row.attempts += 1
                delay = min(BACKOFF_SECONDS * 2**row.attempts, MAX_BACKOFF_SECONDS)
                row.next_attempt_at = timezone.now() + timedelta(seconds=delay)
                row.last_error = str(error)[:300]
                row.save(update_fields=["attempts", "next_attempt_at", "last_error"])
                logger.warning(
                    "outbox_publish_failed",
                    event_id=str(row.event_id),
                    event_type=row.event_type,
                    attempts=row.attempts,
                    error=row.last_error,
                )
                failed += 1
                break
            row.published_at = timezone.now()
            row.save(update_fields=["published_at"])
            sent += 1
    waiting = OutboxEvent.objects.filter(published_at__isnull=True).count()
    return {"sent": sent, "failed": failed, "waiting": waiting}
