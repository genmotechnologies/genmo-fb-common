"""
Tests that need the real thing: PostgreSQL row locks and a Redis cache. (The real
RabbitMQ test is tests/test_outbox_rabbitmq.py.)

Everything else in this suite fakes them, so it runs anywhere. These skip unless the
service is there, and CI starts all three (see .github/workflows/ci.yml). Locally:

    GFC_TEST_DATABASE_URL=postgres://user:password@localhost:5432/gfc
    GFC_TEST_RABBITMQ_URL=amqp://guest:guest@localhost:5672/
    GFC_TEST_REDIS_URL=redis://localhost:6379/0
"""

import os
import threading
import time

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection, connections

from genmo_fb_common import events
from genmo_fb_common.outbox.models import OutboxEvent
from genmo_fb_common.outbox.service import publish_pending

needs_postgres = pytest.mark.skipif(
    not os.environ.get("GFC_TEST_DATABASE_URL"), reason="set GFC_TEST_DATABASE_URL"
)
needs_redis = pytest.mark.skipif(
    not os.environ.get("GFC_TEST_REDIS_URL"), reason="set GFC_TEST_REDIS_URL"
)


@needs_postgres
@pytest.mark.django_db(transaction=True)
def test_two_workers_never_send_the_same_outbox_row(widgets):
    """The row locks the outbox worker relies on exist only on PostgreSQL: SQLite has
    neither them nor SKIP LOCKED."""
    assert connection.vendor == "postgresql"
    for number in range(20):
        events.enqueue_event(
            "widget.made", {"widget_id": f"w-{number}"}, "widget-service"
        )

    sent: list[str] = []
    lock = threading.Lock()

    def slow_send(payload):
        time.sleep(0.05)  # long enough for the other worker to run into the locks
        with lock:
            sent.append(payload.event_id)

    def worker():
        try:
            publish_pending(limit=20, send=slow_send)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(sent) == 20
    assert len(set(sent)) == 20  # no event went out twice
    assert OutboxEvent.objects.filter(published_at__isnull=True).count() == 0


@needs_redis
def test_the_cache_is_a_real_redis_and_keeps_plain_data():
    """FC-06 stores the caller's family in the cache as plain data, so it must survive a
    round trip through Redis."""
    assert "redis" in settings.CACHES["default"]["BACKEND"].lower()
    cache.clear()
    value = {"family_id": "f-1", "role": "parent", "children": ["c-1", "c-2"]}

    cache.set("gfc-test", value, 30)

    assert cache.get("gfc-test") == value
    cache.delete("gfc-test")
    assert cache.get("gfc-test") is None
