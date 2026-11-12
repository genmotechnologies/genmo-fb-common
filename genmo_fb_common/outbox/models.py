from django.db import models
from django.utils import timezone


class OutboxEvent(models.Model):
    """An event waiting to be sent, written in the same transaction as its change."""

    event_id = models.UUIDField(unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()  # the whole envelope, exactly as it will be sent
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["published_at", "next_attempt_at"])]
        ordering = ["id"]

    def __str__(self):
        return f"{self.event_type} {self.event_id}"


class ProcessedEvent(models.Model):
    """An event a consumer has already handled, so a repeat is ignored (FC-03)."""

    consumer = models.CharField(max_length=100)
    event_id = models.CharField(max_length=64)
    processed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["consumer", "event_id"], name="gfc_processed_once"
            )
        ]
