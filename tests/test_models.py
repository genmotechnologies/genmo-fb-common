"""
Tests for base models.
"""

import pytest
from django.db import models
from django.utils import timezone

from genmo_fb_common.models import BaseModel


# Rename to avoid pytest collecting it as a test class
class SampleModel(BaseModel):
    """Sample model for unit tests."""
    name = models.CharField(max_length=100)
    
    class Meta:
        app_label = "tests"


@pytest.mark.django_db  # ADD THIS - allows database access
class TestBaseModel:
    """Tests for BaseModel functionality."""
    
    def test_uuid_primary_key(self):
        """Test that id is UUID."""
        instance = SampleModel(name="Test")
        assert instance.id is not None
        assert len(str(instance.id)) == 36  # UUID format
    
    def test_soft_delete(self):
        """Test soft delete sets deleted_at."""
        instance = SampleModel(name="Test")
        instance.save()  # Must save first
        
        assert instance.deleted_at is None
        assert instance.is_deleted is False
        
        instance.delete()
        
        assert instance.deleted_at is not None
        assert instance.is_deleted is True
    
    def test_restore(self):
        """Test restore clears deleted_at."""
        instance = SampleModel(name="Test")
        instance.save()  # Must save first
        
        instance.delete()
        assert instance.is_deleted is True
        
        instance.restore()
        
        assert instance.deleted_at is None
        assert instance.is_deleted is False
    
    def test_repr(self):
        """Test string representation."""
        instance = SampleModel(name="Test")
        assert "SampleModel" in repr(instance)
        assert str(instance.id) in repr(instance)