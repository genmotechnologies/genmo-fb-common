# genmo-fb-common

Shared library for GenMo Family Banking platform. This is a pip-installable Python package that contains shared code used by all GenMo services.

## Installation
```bash
# From GitHub (recommended for services). Always pin a release tag, never main.
pip install git+https://github.com/genmotechnologies/genmo-fb-common.git@v1.3.0

# For local development (editable mode)
pip install -e /path/to/genmo-fb-common
```

## Usage

### BaseModel
```python
from genmo_fb_common.models import BaseModel
from django.db import models

class Family(BaseModel):
    name = models.CharField(max_length=100)
    
    class Meta(BaseModel.Meta):
        db_table = 'families'
```

### ServiceClient
```python
from genmo_fb_common.clients import ServiceClient
from django.conf import settings

class LimitsClient(ServiceClient):
    base_url = settings.LIMITS_SERVICE_URL
    
    def validate(self, amount: str) -> dict:
        return self.post('/api/v1/limits/validate/', {'amount': amount})
```

### Events (FC-01 to FC-04)

Every event has the same envelope: `event_id`, `event_type`, `occurred_at` (UTC), `version`,
`source_service` and a `data` block. Each event type has one schema in
`genmo_fb_common/event_schemas/<event_type>.json`, and that file is the single source for
the event's name and fields, for the publisher and every consumer.

**Publish from the outbox.** An event that reports a change is written to your own database
in the same transaction as the change, and a worker sends it, so it is not lost if the
message bus is down:

```python
# settings.py
INSTALLED_APPS = [..., "genmo_fb_common.outbox"]     # then: manage.py migrate
GENMO_EVENT_SCHEMA_ENFORCEMENT = "error"             # "warn" (default) logs, "off" skips

from genmo_fb_common.events import enqueue_event

with transaction.atomic():
    chore.approve()
    enqueue_event("chore.instance.approved", {...}, "chores-service")
```

Run the worker beside the web process: `python manage.py publish_outbox` (a loop, `--once`
for one round). It keeps trying while the bus is down and sends each event with the
broker's confirmation. Delivery is at-least-once: an event can arrive twice, so consumers
must handle repeats.

**Consume once.** Record the `event_id`s a consumer has handled and ignore repeats:

```python
from genmo_fb_common.consumers import handle_once, parse_event

def on_message(body):
    event = parse_event(body)                      # checks the schema, reads old or new envelope
    handle_once("gamification.chore-rewards", event, pay_the_reward)
```

**Contract tests.** Publishers validate what they send (`publish_event` and `enqueue_event`
do it; `genmo_fb_common.schemas.validate(event)` does it in a test). Consumers test
themselves against `genmo_fb_common.schemas.example_event("chore.instance.approved")`, and
name the fields they read with `schemas.check_consumer_reads(event_type, fields, always=...)`,
which fails when a field is not in the schema (a renamed or invented one) or may be missing or
null where the consumer indexes it. `publish_event` still sends straight away and returns
`False` if the bus is down: use it only for notices that may be lost.

Tests: `pytest` runs on SQLite. Set `GFC_TEST_DATABASE_URL=postgres://user:pass@host:5432/db` to
run on PostgreSQL, and `GFC_TEST_RABBITMQ_URL=amqp://user:pass@host:5672/` to also run the
real-broker outbox test (bus down, then up: delivered once).

`timestamp` is the old name of `occurred_at`. It is still sent for one release and
`parse_event` accepts either, so a publisher and its consumers can be upgraded separately.

### Ownership (FC-06)
A parent reaches only their own family's children, a child only themselves. The caller comes
from `IdentitySessionMiddleware` (`request.bank_customer_id`); the family comes from
family-service (`GET /api/v1/families/` with the caller's token): one call per request at most,
cached 60 seconds per token, and no call when callers ask about themselves. Set
`FAMILY_SERVICE_URL` (default `http://family-service:8002`).

```python
from genmo_fb_common.ownership import (
    ensure_parent_permission,
    ensure_self_or_own_child,
    get_family_context,
)

child = ensure_self_or_own_child(request, bank_customer_id=child_id)  # None when it's the caller
ensure_parent_permission(request, "approve")  # FA-06: approve, limits_and_cards, send_money
family = get_family_context(request)  # family_id, role, permissions, members, children
```

DRF permission classes (`genmo_fb_common.permissions`): `IsParent`, `IsChild`,
`IsParentOrReadOnly`, `IsFamilyMember` and the object-level `IsSelfOrOwnChild` /
`IsOwnerOrParent` (the owner is `obj.bank_customer_id`, or the view's
`get_owner_bank_customer_id(obj)`).

| Answer | When |
|---|---|
| `403 NOT_YOUR_CHILD` | somebody who is not the caller or their own child (Parent A asking for Parent B's child) |
| `403 FAMILY_PERMISSION_MISSING` | a co-parent without the FA-06 permission for the action |
| `503 FAMILY_UNAVAILABLE` | family-service unreachable: a child can't be confirmed, so nothing is shown |
| `401` | no signed-in caller |

### Token validation (FC-05)
`JwtAuthMiddleware` checks the caller's GenMo access token inside the service, with
identity-service's public key only (RS256): signature, expiry, issuer and audience. It makes
no call to identity-service per request; the key set (`GET /api/v1/auth/jwks/`) is fetched
once and kept for 5 minutes.

```python
MIDDLEWARE = [
    "genmo_fb_common.middleware.RequestIDMiddleware",
    "genmo_fb_common.jwt_auth.JwtAuthMiddleware",  # in place of IdentitySessionMiddleware
    ...
]
```

It sets what `IdentitySessionMiddleware` sets (`request.bank_customer_id`, `request.bank_id`,
`request.session_id`) plus `request.token_claims`, so views, permission classes and the
ownership helpers work unchanged. The token carries no role or family; those come from
family-service through the ownership helpers above.

| Setting | Default |
|---|---|
| `IDENTITY_SERVICE_URL` | `http://identity-service:8001` |
| `JWT_JWKS_URL` | `{IDENTITY_SERVICE_URL}/api/v1/auth/jwks/` |
| `JWT_PUBLIC_KEYS` | not set; `{key id: PEM public key}` to check tokens without fetching the key set |
| `JWT_ISSUER` | `genmo-identity` |
| `JWT_AUDIENCE` | `genmo-services` |
| `JWT_LEEWAY_SECONDS` | `30` |
| `JWT_JWKS_CACHE_SECONDS` | `300` |

| Answer | When |
|---|---|
| request goes on, no caller | no `Authorization: Bearer` header (views answer 401 themselves) |
| `401 INVALID_TOKEN` | tampered, expired, wrong issuer or audience, not an access token, not RS256 |
| `503 AUTH_UNAVAILABLE` | the key set can't be fetched and none is held: nothing can be checked |

A token is trusted until it expires (15 minutes). A session ended at identity-service is not
noticed before then; `IdentitySessionMiddleware`, which asks identity-service on each new
token, does notice. `verify_access_token(token)` gives the claims outside a request.

## Updating genmo-fb-common

To add new functionality:

```bash
# Create branch
git checkout -b feature/COM-XXX-new-feature

# Make changes...

# Test
pytest

# Commit and push
git add .
git commit -m "COM-XXX: Add new feature"
git push origin feature/COM-XXX-new-feature

# Create PR, get approval, merge

# Tag new version
git checkout main
git pull origin main
git tag vX.Y.Z   # the version in pyproject.toml and __init__.py
git push origin vX.Y.Z
```
## Testing installation
```bash
# Create a test directory
mkdir /tmp/test-install
cd /tmp/test-install

# Create virtual environment
python -m venv venv
source venv/bin/activate