"""
Service-to-service identity (PL6 / FC-07).

A scheduled job or an event consumer acts with no parent or child signed in. It proves
which service it is with a service token: identity-service issues one for a client id and
secret (OAuth2 client credentials, ID-04), RS256-signed with the same key as access tokens,
with `typ` "service", `sub` "service:<client id>" and the scopes the client may use. It has
no session and is refused wherever a user's access token is expected, so it can never act
as a parent or child.

Calling side:
    from genmo_fb_common.service_auth import get_service_token
    client = TransferServiceClient(auth_token=get_service_token())

    SERVICE_CLIENT_ID, SERVICE_CLIENT_SECRET   this service's credentials at identity-service
    IDENTITY_SERVICE_URL                       where identity-service is

The token is kept in this process and fetched again a minute before it expires.

Receiving side, on internal endpoints only (`/api/v1/internal/...`, which JwtAuthMiddleware
leaves alone):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [HasServiceScope]
    required_scope = "transfers:scheduled"

A call without a service token, or with a user's token, gets 401; a service token without
the scope gets 403.
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt
import structlog
from django.conf import settings
from rest_framework import authentication, exceptions, permissions

from . import jwt_auth

logger = structlog.get_logger(__name__)

SERVICE = "service"
TOKEN_PATH = "/api/v1/auth/service-token/"
REFRESH_BEFORE_SECONDS = 60
REQUEST_TIMEOUT = 5.0
INTERNAL_PREFIX = jwt_auth.INTERNAL_PREFIX


class KeysUnavailable(exceptions.APIException):
    status_code = 503
    default_detail = "Service tokens can't be checked right now."
    default_code = "AUTH_UNAVAILABLE"


class ServiceTokenUnavailableError(Exception):
    """identity-service did not give this service a token (wrong credentials, or down)."""


# --- calling side ---------------------------------------------------------------------


class _TokenCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.clear()

    def clear(self):
        self._token: Optional[str] = None
        self._expires_at = 0.0

    def get(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            token, lifetime = _fetch_token()
            self._token = token
            self._expires_at = time.monotonic() + max(
                lifetime - REFRESH_BEFORE_SECONDS, 0
            )
            return token


def _fetch_token():
    client_id = getattr(settings, "SERVICE_CLIENT_ID", "")
    secret = getattr(settings, "SERVICE_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise ServiceTokenUnavailableError(
            "SERVICE_CLIENT_ID and SERVICE_CLIENT_SECRET are not set"
        )
    url = (
        getattr(settings, "IDENTITY_SERVICE_URL", "http://identity-service:8001")
        + TOKEN_PATH
    )
    try:
        response = httpx.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ServiceTokenUnavailableError(
            f"identity-service unreachable: {exc}"
        ) from exc
    if response.status_code != 200:
        # the answer's body is not logged: it may echo what was sent
        logger.warning("service_token_refused", status=response.status_code)
        raise ServiceTokenUnavailableError(
            f"identity-service answered {response.status_code}"
        )
    body = response.json()
    return body["access_token"], int(body.get("expires_in", 300))


_cache = _TokenCache()


def get_service_token() -> str:
    """This service's token, fetched once and kept until shortly before it expires."""
    return _cache.get()


def clear_service_token_cache() -> None:
    _cache.clear()


# --- receiving side -------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceCaller:
    """The service behind a service token; never a parent or child."""

    name: str
    scopes: frozenset

    is_authenticated = True
    is_anonymous = False
    bank_customer_id = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def verify_service_token(token: str) -> dict:
    """
    The claims of a genuine, unexpired service token. Raises jwt_auth.InvalidTokenError
    (a user's access token included) or jwt_auth.KeysUnavailableError.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise jwt_auth.InvalidTokenError("Malformed token") from exc
    if header.get("alg") != jwt_auth.ALGORITHM:
        raise jwt_auth.InvalidTokenError("Unexpected signing algorithm")
    kid = header.get("kid")
    key = jwt_auth._configured_key(kid) or jwt_auth._key_set.get(kid)
    if key is None:
        raise jwt_auth.InvalidTokenError("Unknown signing key")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[jwt_auth.ALGORITHM],
            audience=getattr(settings, "JWT_AUDIENCE", "genmo-services"),
            issuer=getattr(settings, "JWT_ISSUER", "genmo-identity"),
            leeway=getattr(settings, "JWT_LEEWAY_SECONDS", 30),
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise jwt_auth.InvalidTokenError(str(exc)) from exc
    if claims.get("typ") != SERVICE or not str(claims["sub"]).startswith("service:"):
        raise jwt_auth.InvalidTokenError("Not a service token")
    return claims


class ServiceTokenAuthentication(authentication.BaseAuthentication):
    """DRF: the caller is a service with a genuine service token, or the request is 401."""

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise exceptions.NotAuthenticated("A service token is required.")
        try:
            claims = verify_service_token(header[7:].strip())
        except jwt_auth.InvalidTokenError as exc:
            logger.info("service_token_refused", reason=str(exc))
            raise exceptions.AuthenticationFailed("The service token is not valid.")
        except jwt_auth.KeysUnavailableError:
            raise KeysUnavailable()
        caller = ServiceCaller(
            name=claims["sub"][len("service:") :],
            scopes=frozenset(str(claims.get("scope", "")).split()),
        )
        return caller, claims

    def authenticate_header(self, request):
        return "Bearer"


class HasServiceScope(permissions.BasePermission):
    """The service token carries the view's `required_scope` (fail closed without one)."""

    message = "The service token does not allow this."

    def has_permission(self, request, view):
        caller = request.user
        required = getattr(view, "required_scope", None)
        return (
            isinstance(caller, ServiceCaller)
            and bool(required)
            and caller.has_scope(required)
        )


try:  # the OpenAPI description of service tokens, for services that use drf-spectacular
    from drf_spectacular.extensions import OpenApiAuthenticationExtension
except ImportError:  # pragma: no cover
    pass
else:

    class ServiceTokenScheme(OpenApiAuthenticationExtension):
        target_class = "genmo_fb_common.service_auth.ServiceTokenAuthentication"
        name = "ServiceToken"

        def get_security_definition(self, auto_schema):
            return {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "A service token from identity-service (client "
                "credentials, PL6); never a parent's or child's token.",
            }
