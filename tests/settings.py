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

# Event schemas are checked strictly in the library's own tests.
GENMO_EVENT_SCHEMA_ENFORCEMENT = "error"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
