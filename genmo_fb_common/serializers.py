"""
Base serializers for GenMo services.
"""

from rest_framework import serializers


class BaseModelSerializer(serializers.ModelSerializer):
    """
    Base serializer with common fields.

    Automatically includes id, created_at, updated_at.
    Excludes deleted_at from output.

    Usage:
        class FamilySerializer(BaseModelSerializer):
            class Meta(BaseModelSerializer.Meta):
                model = Family
                fields = BaseModelSerializer.Meta.fields + ['name', 'created_by']
    """

    id = serializers.UUIDField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        fields = ["id", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class ErrorSerializer(serializers.Serializer):
    """
    Serializer for error responses.

    Used by exception handler.
    """

    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.DictField(required=False)
