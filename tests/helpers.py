"""Shared helpers for the event tests."""

from genmo_fb_common.events import EventPayload


def widget(**data):
    return EventPayload(
        event_type="widget.made",
        source_service="widget-service",
        data=data or {"widget_id": "w-1"},
    )


def widget_with(data):
    return EventPayload("widget.made", "widget-service", data)
