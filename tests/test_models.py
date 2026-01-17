"""
Tests for base models.
"""

from unittest.mock import patch

from django.db import models
from django.utils import timezone

from genmo_fb_common.models import BaseModel


class SampleModel(BaseModel):
    """Sample model for unit tests."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"


class TestBaseModel:
    """Tests for BaseModel functionality."""

    def test_uuid_primary_key(self):
        """Test that id is UUID."""
        instance = SampleModel(name="Test")
        assert instance.id is not None
        assert len(str(instance.id)) == 36  # UUID format

    def test_soft_delete(self):
        """Test soft delete sets deleted_at without saving to DB."""
        instance = SampleModel(name="Test")

        assert instance.deleted_at is None
        assert instance.is_deleted is False

        # Mock save to avoid database
        with patch.object(instance, "save"):
            instance.deleted_at = timezone.now()

        assert instance.deleted_at is not None
        assert instance.is_deleted is True

    def test_restore(self):
        """Test restore clears deleted_at without saving to DB."""
        instance = SampleModel(name="Test")

        # Set as deleted
        instance.deleted_at = timezone.now()
        assert instance.is_deleted is True

        # Restore
        with patch.object(instance, "save"):
            instance.deleted_at = None

        assert instance.deleted_at is None
        assert instance.is_deleted is False

    def test_repr(self):
        """Test string representation."""
        instance = SampleModel(name="Test")
        assert "SampleModel" in repr(instance)
        assert str(instance.id) in repr(instance)

    def test_delete_method_calls_save(self):
        """Test that delete() calls save with correct fields."""
        instance = SampleModel(name="Test")

        with patch.object(instance, "save") as mock_save:
            instance.delete()

        assert instance.deleted_at is not None
        mock_save.assert_called_once_with(update_fields=["deleted_at", "updated_at"])

    def test_restore_method_calls_save(self):
        """Test that restore() calls save with correct fields."""
        instance = SampleModel(name="Test")
        instance.deleted_at = timezone.now()

        with patch.object(instance, "save") as mock_save:
            instance.restore()

        assert instance.deleted_at is None
        mock_save.assert_called_once_with(update_fields=["deleted_at", "updated_at"])
