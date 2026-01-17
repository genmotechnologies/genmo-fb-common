"""
Base model for all GenMo entities.
"""

import uuid
from django.db import models
from django.utils import timezone

from .managers import ActiveManager


class BaseModel(models.Model):
    """
    Abstract base model providing:
    - UUID primary key
    - Created/updated timestamps
    - Soft delete functionality
    
    All GenMo models should inherit from this.
    
    Example:
        class Family(BaseModel):
            name = models.CharField(max_length=100)
            
            class Meta(BaseModel.Meta):
                db_table = 'families'
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the record was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When the record was last updated"
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the record was soft-deleted (null = active)"
    )

    # Default manager returns all records (including deleted)
    all_objects = models.Manager()
    
    # Active manager excludes soft-deleted records
    objects = ActiveManager()

    class Meta:
        abstract = True
        ordering = ['-created_at']

    def delete(self, hard: bool = False, *args, **kwargs) -> None:
        """
        Delete the record.
        
        Args:
            hard: If True, permanently delete. If False (default), soft delete.
        """
        if hard:
            super().delete(*args, **kwargs)
        else:
            self.deleted_at = timezone.now()
            self.save(update_fields=['deleted_at', 'updated_at'])

    def restore(self) -> None:
        """Restore a soft-deleted record."""
        self.deleted_at = None
        self.save(update_fields=['deleted_at', 'updated_at'])

    @property
    def is_deleted(self) -> bool:
        """Check if record is soft-deleted."""
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.id}>"