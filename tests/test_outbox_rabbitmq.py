"""
FC-02 against a real RabbitMQ: with the bus down the event waits, and once the bus is up it
is delivered exactly once. Runs only when GFC_TEST_RABBITMQ_URL is set, for example
amqp://genmo:genmo_dev_password@127.0.0.1:5672/ (the local stack's broker).
"""

import json
import os
import uuid

import pika
import pytest
from django.utils import timezone

from genmo_fb_common import events
from genmo_fb_common.outbox.models import OutboxEvent
from genmo_fb_common.outbox.service import publish_pending

URL = os.environ.get("GFC_TEST_RABBITMQ_URL")
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not URL, reason="GFC_TEST_RABBITMQ_URL is not set"),
]
DOWN = "amqp://guest:guest@127.0.0.1:1/"  # nothing listens on port 1


@pytest.fixture
def queue(settings):
    """A queue bound to widget.made on the platform's exchange."""
    name = f"gfc-test-{uuid.uuid4().hex[:8]}"
    connection = pika.BlockingConnection(pika.URLParameters(URL))
    channel = connection.channel()
    channel.exchange_declare(
        exchange="genmo.events", exchange_type="topic", durable=True
    )
    channel.queue_declare(queue=name, auto_delete=True)
    channel.queue_bind(queue=name, exchange="genmo.events", routing_key="widget.made")
    yield channel, name
    channel.queue_delete(queue=name)
    connection.close()


def drain(channel, name):
    bodies = []
    while True:
        method, _, body = channel.basic_get(queue=name, auto_ack=True)
        if method is None:
            return bodies
        bodies.append(json.loads(body))


def test_an_event_written_while_the_bus_is_down_arrives_once_when_it_is_up(
    widgets, settings, queue
):
    channel, name = queue
    settings.CELERY_BROKER_URL = DOWN
    event_id = events.enqueue_event(
        "widget.made", {"widget_id": "w-1"}, "widget-service"
    )

    assert publish_pending()["failed"] == 1  # the bus is down: the event waits
    assert drain(channel, name) == []

    settings.CELERY_BROKER_URL = URL
    OutboxEvent.objects.update(next_attempt_at=timezone.now())
    assert publish_pending()["sent"] == 1
    assert publish_pending()["sent"] == 0  # and is not sent again

    (message,) = drain(channel, name)
    assert message["event_id"] == event_id
    assert message["occurred_at"] and message["data"] == {"widget_id": "w-1"}
