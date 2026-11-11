"""
Event schemas (FC-01, FC-04): one published schema per event type, and the one source of
truth for event names and field names between publishers and consumers.

A schema is a JSON file in `genmo_fb_common/event_schemas/<event_type>.json`:

    {
      "event_type": "chore.instance.approved",
      "version": 1,
      "source_service": "chores-service",
      "description": "...",
      "data": { ...JSON Schema (draft 2020-12) for the event's `data` block... },
      "examples": [ { ...a valid `data` block... } ]
    }

Publishers validate what they send (`validate`, or `publish_event` / `enqueue_event`, which
do it for them). Consumers test themselves against `example_event` and read only fields the
schema declares. Changing a field is a change to the file, so it is reviewed once, here.
"""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).parent / "event_schemas"
ENVELOPE_FIELDS = (
    "event_id",
    "event_type",
    "occurred_at",
    "version",
    "source_service",
    "data",
)
# ISO 8601 in UTC, as the platform writes times (2026-10-22T09:30:00.123456Z).
UTC_TIME = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"
UUID = r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$"


class EventSchemaError(ValueError):
    """An event does not match its schema, or has none."""


@lru_cache(maxsize=1)
def _load() -> dict:
    found = {}
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if path.stem != schema["event_type"]:
            raise EventSchemaError(
                f"{path.name} describes {schema['event_type']}: name the file after it"
            )
        found[schema["event_type"]] = schema
    return found


def event_types() -> list[str]:
    return sorted(_load())


def get_schema(event_type: str) -> dict:
    try:
        return _load()[event_type]
    except KeyError:
        raise EventSchemaError(
            f"No schema for event type '{event_type}'. Add "
            f"genmo_fb_common/event_schemas/{event_type}.json"
        ) from None


@lru_cache(maxsize=None)
def _validator(event_type: str) -> Draft202012Validator:
    return Draft202012Validator(get_schema(event_type)["data"])


def _first_problem(errors) -> str:
    problem = sorted(errors, key=lambda e: list(e.absolute_path))[0]
    where = ".".join(str(part) for part in problem.absolute_path) or "data"
    return f"{where}: {problem.message}"


def validate_data(event_type: str, data: dict) -> None:
    """Raise EventSchemaError unless `data` matches the schema for `event_type`."""
    errors = list(_validator(event_type).iter_errors(data))
    if errors:
        raise EventSchemaError(f"{event_type}: {_first_problem(errors)}")


def validate(event: dict) -> None:
    """Raise EventSchemaError unless the whole envelope and its data are valid."""
    missing = [name for name in ENVELOPE_FIELDS if name not in event]
    if missing:
        raise EventSchemaError(f"envelope is missing {', '.join(missing)}")
    schema = get_schema(event["event_type"])
    if event["source_service"] != schema["source_service"]:
        raise EventSchemaError(
            f"{event['event_type']} is published by {schema['source_service']}, "
            f"not {event['source_service']}"
        )
    if event["version"] != schema["version"]:
        raise EventSchemaError(
            f"{event['event_type']} is at version {schema['version']}, "
            f"not {event['version']}"
        )
    validate_data(event["event_type"], event["data"])


def example_event(event_type: str, **overrides) -> dict:
    """
    A valid event of this type, for a consumer's tests: the schema's first example as
    `data`, with `overrides` replacing individual data fields.
    """
    schema = get_schema(event_type)
    from .events import EventPayload

    data = {**schema["examples"][0], **overrides}
    event = EventPayload(
        event_type=event_type, source_service=schema["source_service"], data=data
    ).to_dict()
    validate(event)
    return event


def check_consumer_reads(event_type: str, fields, *, always=()) -> None:
    """
    The contract test between a publisher and one consumer (FC-04): call it in the
    consumer's tests with the `data` fields the consumer reads. It raises EventSchemaError if
    the schema does not declare one of them, which is how a renamed or invented field
    (`sender_bank_customer_id` against `from_bank_customer_id`) is caught. Fields the
    consumer indexes without a default belong in `always`: they must be required and not
    nullable, or the consumer can fail on a valid event.
    """
    data = get_schema(event_type)["data"]
    properties = data["properties"]
    unknown = sorted(set(fields) - set(properties))
    if unknown:
        raise EventSchemaError(
            f"{event_type} has no field {', '.join(unknown)}; it declares "
            f"{', '.join(sorted(properties))}"
        )
    fragile = sorted(
        name
        for name in always
        if name not in data["required"] or "null" in _types(properties[name])
    )
    if fragile:
        raise EventSchemaError(
            f"{event_type}: {', '.join(fragile)} may be missing or null, "
            "so the consumer must not index it without a default"
        )


def _types(schema: dict) -> list:
    kind = schema.get("type", [])
    return kind if isinstance(kind, list) else [kind]
