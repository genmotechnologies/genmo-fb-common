# genmo-fb-common

Shared library for GenMo Family Banking platform. This is a pip-installable Python package that contains shared code used by all GenMo services.

## Installation
```bash
# From GitHub (recommended for services)
pip install git+https://github.com/genmotechnologies/genmo-fb-common.git

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

### Event Publishing
```python
from genmo_fb_common.events import EventPayload, publish_event

payload = EventPayload(
    event_type="transfer.completed",
    source_service="transfer-service",
    data={"transfer_id": "123", "amount": "50.00"}
)
publish_event(payload)
```

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
git tag v1.1.0
git push origin v1.1.0
```
## Testing installation
```bash
# Create a test directory
mkdir /tmp/test-install
cd /tmp/test-install

# Create virtual environment
python -m venv venv
source venv/bin/activate