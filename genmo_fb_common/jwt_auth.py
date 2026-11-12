"""
Token validation (FC-05 / PL4): GenMo access tokens are checked here, in the service, with
identity-service's public key only. No call to identity-service per request.

identity-service signs access tokens with RS256 (ID-01) and publishes the public key as a
JWKS at `GET /api/v1/auth/jwks/`. A token says who (`sub`, the bank customer id), at which
bank (`bank_id`) and in which session (`sid`); nothing else. Role and family are not in the
token: they come from family-service through `genmo_fb_common.ownership` (FC-06).

What is checked: the signature (RS256 only), expiry, issuer, audience, that it is an access
token, and that `sub` and `sid` are there.

Settings (all optional):
    IDENTITY_SERVICE_URL     where identity-service is (default http://identity-service:8001)
    JWT_JWKS_URL             the key set (default {IDENTITY_SERVICE_URL}/api/v1/auth/jwks/)
    JWT_PUBLIC_KEYS          {key id: PEM public key}; when set, no key set is fetched
    JWT_ISSUER               default "genmo-identity"   (identity-service's JWT_ISSUER)
    JWT_AUDIENCE             default "genmo-services"   (identity-service's JWT_AUDIENCE)
    JWT_LEEWAY_SECONDS       clock difference allowed between services (default 30)
    JWT_JWKS_CACHE_SECONDS   how long a fetched key set is kept (default 300)

Usage, in MIDDLEWARE, in place of IdentitySessionMiddleware:
    "genmo_fb_common.jwt_auth.JwtAuthMiddleware",

It sets the same attributes IdentitySessionMiddleware sets (`bank_customer_id`, `bank_id`,
`session_id`), so views, permission classes and ownership checks work unchanged.

Answers:
    no Authorization header   the request goes on with no caller (views answer 401 themselves)
    401 INVALID_TOKEN         tampered, expired, wrong issuer or audience, not an access token
    503 AUTH_UNAVAILABLE      the key set could not be fetched and none is held (fail closed)

Known limit: a token is trusted until it expires (15 minutes). A session ended or refreshed at
identity-service is not noticed here before then; IdentitySessionMiddleware, which asks
identity-service, does notice.
"""

import threading
import time
from typing import Optional

import httpx
import jwt
import structlog
from django.conf import settings
from django.http import JsonResponse

logger = structlog.get_logger(__name__)

ALGORITHM = "RS256"
ACCESS = "access"
REQUEST_TIMEOUT = 5.0
# an unknown key id fetches the key set again, but not more often than this: a caller
# sending made-up key ids must not turn every request into a call to identity-service
MIN_REFETCH_SECONDS = 30
NO_KEYS_RETRY_SECONDS = 5


class InvalidTokenError(Exception):
    """The token is not a genuine, unexpired GenMo access token (401)."""


class KeysUnavailableError(Exception):
    """The key set could not be fetched and none is held, so no token can be checked (503)."""


class _KeySet:
    """identity-service's public keys by key id, kept in this process."""

    def __init__(self):
        self._lock = threading.Lock()
        self.clear()

    def clear(self):
        self._keys = {}
        self._fetched_at = 0.0
        self._tried_at = 0.0

    def get(self, kid: Optional[str]):
        """The public key with this key id; None when identity-service has no such key."""
        now = time.monotonic()
        max_age = getattr(settings, "JWT_JWKS_CACHE_SECONDS", 300)
        with self._lock:
            fresh = self._keys and now - self._fetched_at < max_age
            # with no keys held every request is refused, so try again sooner
            wait = MIN_REFETCH_SECONDS if self._keys else NO_KEYS_RETRY_SECONDS
            may_fetch = not self._tried_at or now - self._tried_at >= wait
            if (not fresh or kid not in self._keys) and may_fetch:
                self._fetch(now)
            if not self._keys:
                raise KeysUnavailableError()
            return self._keys.get(kid)

    def _fetch(self, now: float) -> None:
        self._tried_at = now
        url = getattr(settings, "JWT_JWKS_URL", None) or (
            getattr(settings, "IDENTITY_SERVICE_URL", "http://identity-service:8001")
            + "/api/v1/auth/jwks/"
        )
        try:
            response = httpx.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            keys = {
                key.key_id: key.key
                for key in jwt.PyJWKSet.from_dict(response.json()).keys
                if key.key_id
            }
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError) as exc:
            # the keys already held stay in use: a key changes rarely, and identity-service
            # being down must not sign everybody out
            logger.warning("jwks_fetch_failed", url=url, error=str(exc))
            return
        if keys:
            self._keys = keys
            self._fetched_at = now


_key_set = _KeySet()


def clear_key_cache() -> None:
    """Forget the fetched key set (tests, or after identity-service's key was replaced)."""
    _key_set.clear()


def _configured_key(kid: Optional[str]):
    """The key from JWT_PUBLIC_KEYS, or None when that setting is not used."""
    configured = getattr(settings, "JWT_PUBLIC_KEYS", None)
    if not configured:
        return None
    pem = configured.get(kid)
    if pem is None:
        raise InvalidTokenError("Unknown signing key")
    return pem.replace("\\n", "\n")


def verify_access_token(token: str) -> dict:
    """
    The claims of a genuine, unexpired GenMo access token.
    Raises InvalidTokenError (401) or KeysUnavailableError (503).
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Malformed token") from exc
    # only RS256: `none`, and HS256 signed with the public key, are refused before any key
    # is looked up
    if header.get("alg") != ALGORITHM:
        raise InvalidTokenError("Unexpected signing algorithm")
    kid = header.get("kid")

    key = _configured_key(kid)
    if key is None:
        key = _key_set.get(kid)
        if key is None:
            raise InvalidTokenError("Unknown signing key")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=getattr(settings, "JWT_AUDIENCE", "genmo-services"),
            issuer=getattr(settings, "JWT_ISSUER", "genmo-identity"),
            leeway=getattr(settings, "JWT_LEEWAY_SECONDS", 30),
            options={"require": ["exp", "iat", "sub", "sid"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if claims.get("typ") != ACCESS:
        raise InvalidTokenError("Not an access token")
    return claims


def _refuse(status: int, code: str, message: str) -> JsonResponse:
    response = JsonResponse(
        {"error": {"code": code, "message": message, "details": {}}}, status=status
    )
    if status == 401:
        response["WWW-Authenticate"] = "Bearer"
    return response


class JwtAuthMiddleware:
    """
    Checks the caller's GenMo access token locally (RS256, identity-service's public key) and
    puts the caller on the request:

        request.bank_customer_id   the token's `sub`
        request.bank_id            the token's `bank_id`
        request.session_id         the token's `sid`
        request.token_claims       every claim

    All four are None when the request carries no Bearer token. A Bearer token that is not
    genuine is answered here with 401, so no view sees it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.bank_customer_id = None
        request.bank_id = None
        request.session_id = None
        request.token_claims = None

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return self.get_response(request)

        try:
            claims = verify_access_token(auth_header[7:].strip())
        except InvalidTokenError as exc:
            logger.info("token_refused", reason=str(exc))
            return _refuse(401, "INVALID_TOKEN", "The access token is not valid.")
        except KeysUnavailableError:
            return _refuse(
                503,
                "AUTH_UNAVAILABLE",
                "Sign-in can't be checked right now. Please try again.",
            )

        request.bank_customer_id = claims["sub"]
        request.bank_id = claims.get("bank_id")
        request.session_id = claims["sid"]
        request.token_claims = claims
        return self.get_response(request)
