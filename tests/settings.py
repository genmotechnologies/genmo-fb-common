"""
Minimal Django settings for testing genmo-fb-common.
"""

import os
from urllib.parse import urlparse

SECRET_KEY = "test-secret-key-not-for-production"

DEBUG = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "genmo_fb_common.outbox",
    "tests",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# GFC_TEST_DATABASE_URL=postgres://user:password@host:5432/dbname runs the tests on
# PostgreSQL, which has the row locks the outbox worker relies on.
if os.environ.get("GFC_TEST_DATABASE_URL"):
    _url = urlparse(os.environ["GFC_TEST_DATABASE_URL"])
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _url.path.lstrip("/"),
        "USER": _url.username,
        "PASSWORD": _url.password,
        "HOST": _url.hostname,
        "PORT": _url.port or 5432,
    }

# GFC_TEST_REDIS_URL=redis://host:6379/0 uses a running Redis as the cache, as CI does.
# Without it the tests use the in-memory cache. (The RabbitMQ test has its own variable,
# GFC_TEST_RABBITMQ_URL, see tests/test_outbox_rabbitmq.py.)
if os.environ.get("GFC_TEST_REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["GFC_TEST_REDIS_URL"],
        }
    }

# Event schemas are checked strictly in the library's own tests.
GENMO_EVENT_SCHEMA_ENFORCEMENT = "error"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
