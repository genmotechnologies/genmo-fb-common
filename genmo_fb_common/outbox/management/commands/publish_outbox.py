"""
FC-02: the outbox worker. Sends the events the service wrote to its outbox, and keeps
trying while the message bus is down.

    python manage.py publish_outbox            # a loop, until stopped
    python manage.py publish_outbox --once     # one round, then exit
"""

import time

from django.core.management.base import BaseCommand

from genmo_fb_common.outbox.service import publish_pending


class Command(BaseCommand):
    help = "Send the events waiting in the outbox to the message bus (FC-02)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=2.0)
        parser.add_argument("--batch", type=int, default=100)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, interval, batch, once, **options):
        while True:
            result = publish_pending(limit=batch)
            if result["sent"] or result["failed"]:
                self.stdout.write(str(result))
            if once:
                return
            time.sleep(interval)
