"""
Idempotent consumers (FC-03): a consumer records the event_ids it has handled and
ignores repeats, so an event sent twice (a retry, a broker redelivery) acts once.

    from genmo_fb_common.consumers import handle_once, parse_event

    def on_message(body):
        event = parse_event(body)
        handle_once("gamification.chore-rewards", event, pay_the_reward)

The handler and the record share one database transaction: if the handler fails, nothing
is recorded and the event is handled when it is delivered again. Needs
"genmo_fb_common.outbox" in INSTALLED_APPS.
"""

import json
from typing import Callable, Union

import structlog
from django.db import IntegrityError, transaction

from . import events, schemas

logger = structlog.get_logger(__name__)


def parse_event(body: Union[bytes, str, dict]) -> "events.EventPayload":
    """
    Read an event from a message body, in the current or the old envelope, and check it
    against its schema (as GENMO_EVENT_SCHEMA_ENFORCEMENT says). Raises EventSchemaError
    for a body that is not an event at all.
    """
    try:
        raw = body if isinstance(body, dict) else json.loads(body)
        payload = events.EventPayload.from_dict(raw)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise schemas.EventSchemaError(f"not an event: {error}") from error
    events.validate_event(payload)
    return payload


def handle_once(consumer: str, event: "events.EventPayload", handler: Callable) -> bool:
    """
    Run `handler(event)` unless this `consumer` already handled this event. Returns True
    when it ran and False for a repeat. `consumer` names the handler, so two consumers in
    one service each handle the same event once.
    """
    from .outbox.models import ProcessedEvent

    with transaction.atomic():
        try:
            # A savepoint, so a repeat does not poison the surrounding transaction.
            with transaction.atomic():
                ProcessedEvent.objects.create(
                    consumer=consumer, event_id=event.event_id
                )
        except IntegrityError:
            logger.info(
                "event_already_handled", consumer=consumer, event_id=event.event_id
            )
            return False
        handler(event)
    return True
