"""
Event publishing for cross-service communication (FC-01, FC-02, FC-03).

Every event has the same envelope: `event_id`, `event_type`, `occurred_at` (UTC),
`version`, `source_service` and a `data` block that matches the one published schema for
that event type (see `genmo_fb_common.event_schemas` and `genmo_fb_common.schemas`).

Two ways to publish:

    enqueue_event(...)   writes the event to the service's own database, in the same
                         transaction as the change, and a worker sends it later
                         (`manage.py publish_outbox`). Use this for every event that
                         reports a change. Needs "genmo_fb_common.outbox" in INSTALLED_APPS.
    publish_event(...)   sends it straight away and returns False if the bus is down, in
                         which case the event is lost. For fire-and-forget notices only.

Consumers take events with `parse_event` and `handle_once` (see consumers.py).

The old envelope field `timestamp` is still sent beside `occurred_at` for one release, and
`parse_event` accepts either, so publishers and consumers can be upgraded separately.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pika
import structlog
from django.conf import settings

from . import schemas

logger = structlog.get_logger(__name__)

EXCHANGE = "genmo.events"
ENVELOPE_VERSION = 1


def utc_now() -> str:
    """Now in UTC, as the contract writes times: ISO 8601 with a trailing Z."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class EventPayload:
    """
    The envelope every event uses.

    Usage:
        payload = EventPayload(
            event_type="transfer.completed",
            source_service="transfer-service",
            data={"transfer_id": str(transfer.id), "amount": "50.00"},
        )
    """

    event_type: str
    source_service: str
    data: dict
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = ""
    version: int = ENVELOPE_VERSION
    # Deprecated spelling of occurred_at, accepted for one release.
    timestamp: Optional[str] = None

    def __post_init__(self):
        if not self.occurred_at:
            self.occurred_at = self.timestamp or utc_now()
        self.timestamp = self.occurred_at

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "version": self.version,
            "source_service": self.source_service,
            "data": self.data,
            # Deprecated: the old name of occurred_at, until every consumer reads the new.
            "timestamp": self.occurred_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, body: dict) -> "EventPayload":
        """Read an event in the current or the old envelope (`timestamp`, version "1.0")."""
        version = body.get("version", ENVELOPE_VERSION)
        if isinstance(version, str):
            version = int(version.split(".")[0] or ENVELOPE_VERSION)
        return cls(
            event_type=body["event_type"],
            source_service=body["source_service"],
            data=body.get("data") or {},
            event_id=body["event_id"],
            occurred_at=body.get("occurred_at") or body.get("timestamp") or "",
            version=version,
        )


def validate_event(payload: EventPayload) -> None:
    """
    Check the event against its published schema.

    GENMO_EVENT_SCHEMA_ENFORCEMENT picks what a mismatch does: "error" raises
    EventSchemaError, "warn" (the default while services move over) logs it, "off" skips
    the check. An event type with no schema is a mismatch too.
    """
    mode = getattr(settings, "GENMO_EVENT_SCHEMA_ENFORCEMENT", "warn")
    if mode == "off":
        return
    try:
        schemas.validate(payload.to_dict())
    except schemas.EventSchemaError as error:
        if mode == "error":
            raise
        logger.warning(
            "event_schema_mismatch",
            event_type=payload.event_type,
            source_service=payload.source_service,
            problem=str(error),
        )


def get_rabbitmq_connection():
    """RabbitMQ connection from settings.CELERY_BROKER_URL."""
    broker_url = getattr(
        settings, "CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672/"
    )
    params = pika.URLParameters(broker_url)
    return pika.BlockingConnection(params)


def send_event(payload: EventPayload, exchange: str = EXCHANGE) -> None:
    """
    Send one event and raise if the broker did not take it. The channel waits for the
    broker's confirmation, so a return means the message is safely queued.
    """
    connection = get_rabbitmq_connection()
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        channel.basic_publish(
            exchange=exchange,
            routing_key=payload.event_type,
            body=payload.to_json(),
            properties=pika.BasicProperties(
                delivery_mode=2, content_type="application/json"  # persistent
            ),
        )
    finally:
        connection.close()


def publish_event(payload: EventPayload, exchange: str = EXCHANGE) -> bool:
    """
    Send an event now. Returns True when the broker confirmed it, False otherwise, and a
    False means the event is lost: use `enqueue_event` for anything that reports a change.
    """
    validate_event(payload)
    try:
        send_event(payload, exchange)
    except Exception as error:
        logger.error(
            "event_publish_failed", event_type=payload.event_type, error=str(error)
        )
        return False
    logger.info(
        "event_published",
        event_id=payload.event_id,
        event_type=payload.event_type,
        exchange=exchange,
    )
    return True


def enqueue_event(event_type: str, data: dict, source_service: str) -> str:
    """
    Record an event in this service's outbox, inside the current database transaction,
    so it exists exactly when the change it reports does. `manage.py publish_outbox`
    sends it, and keeps trying while the bus is down. Returns the event_id.
    """
    from .outbox.service import enqueue

    return enqueue(
        EventPayload(event_type=event_type, source_service=source_service, data=data)
    )


def publish_event_async(event_type: str, data: dict, source_service: str) -> None:
    """Kept for callers that used it: the event goes to the outbox (`enqueue_event`)."""
    enqueue_event(event_type, data, source_service)
