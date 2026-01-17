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