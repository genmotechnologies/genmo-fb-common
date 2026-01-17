"""
Custom Django model managers.
"""

from django.db import models


class ActiveManager(models.Manager):
    """
    Manager that excludes soft-deleted records.

    Usage:
        # Returns only non-deleted records
        Family.objects.all()

        # To include deleted records
        Family.all_objects.all()
    """

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)
