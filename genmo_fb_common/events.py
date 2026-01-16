"""
Event publishing utilities for cross-service communication.
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

import pika
import structlog
from django.conf import settings


logger = structlog.get_logger(__name__)


@dataclass
class EventPayload:
    """
    Standard event payload structure.
    
    All events MUST use this structure for consistency.
    
    Usage:
        payload = EventPayload(
            event_type="transfer.completed",
            source_service="transfer-service",
            data={
                "transfer_id": str(transfer.id),
                "amount": "50.00"
            }
        )
        publish_event(payload)
    """
    event_type: str
    source_service: str
    data: dict
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    version: str = "1.0"
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


def get_rabbitmq_connection():
    """
    Get RabbitMQ connection.
    
    Uses settings.CELERY_BROKER_URL for connection.
    """
    broker_url = getattr(settings, 'CELERY_BROKER_URL', 'amqp://guest:guest@localhost:5672/')
    params = pika.URLParameters(broker_url)
    return pika.BlockingConnection(params)


def publish_event(payload: EventPayload, exchange: str = "genmo.events") -> bool:
    """
    Publish an event to RabbitMQ.
    
    Args:
        payload: EventPayload object
        exchange: RabbitMQ exchange name (default: genmo.events)
        
    Returns:
        True if published successfully, False otherwise
        
    Example:
        payload = EventPayload(
            event_type="family.member.added",
            source_service="family-service",
            data={
                "family_id": str(family.id),
                "member_id": str(member.id)
            }
        )
        publish_event(payload)
    """
    try:
        connection = get_rabbitmq_connection()
        channel = connection.channel()
        
        # Declare exchange (topic type for routing)
        channel.exchange_declare(
            exchange=exchange,
            exchange_type='topic',
            durable=True
        )
        
        # Publish message
        channel.basic_publish(
            exchange=exchange,
            routing_key=payload.event_type,
            body=payload.to_json(),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Persistent
                content_type='application/json'
            )
        )
        
        connection.close()
        
        logger.info(
            "event_published",
            event_id=payload.event_id,
            event_type=payload.event_type,
            exchange=exchange
        )
        
        return True
        
    except Exception as e:
        logger.error(
            "event_publish_failed",
            event_type=payload.event_type,
            error=str(e)
        )
        return False


def publish_event_async(event_type: str, data: dict, source_service: str) -> None:
    """
    Publish event via Celery task (non-blocking).
    
    This is a convenience function that creates the payload and
    schedules it for async publishing.
    
    Usage:
        from genmo_fb_common.events import publish_event_async
        
        publish_event_async(
            event_type="transfer.completed",
            data={"transfer_id": "123", "amount": "50.00"},
            source_service="transfer-service"
        )
    """
    # Import here to avoid circular imports
    from genmo_fb_common.tasks import publish_event_task
    
    payload = EventPayload(
        event_type=event_type,
        source_service=source_service,
        data=data
    )
    
    publish_event_task.delay(payload.to_dict())