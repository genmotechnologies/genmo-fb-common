"""
Tests for token validation (FC-05): RS256 access tokens checked with identity-service's
public key only.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory, override_settings

from genmo_fb_common import jwt_auth
from genmo_fb_common.jwt_auth import (
    InvalidTokenError,
    JwtAuthMiddleware,
    KeysUnavailableError,
    clear_key_cache,
    verify_access_token,
)

ISSUER = "genmo-identity"
AUDIENCE = "genmo-services"


class Key:
    """An RSA key as identity-service holds it, with its JWKS entry."""

    def __init__(self, kid):
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_pem = (
            self.private.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

    def jwk(self):
        entry = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key())
        )
        entry.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return entry

    def token(self, **changes):
        now = datetime.now(timezone.utc)
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "CUST-1",
            "bank_id": "BANK-1",
            "sid": "session-1",
            "typ": "access",
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "jti": uuid.uuid4().hex,
        }
        claims.update(changes)
        claims = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(
            claims, self.private, algorithm="RS256", headers={"kid": self.kid}
        )


@pytest.fixture(scope="module")
def key():
    return Key("key-1")


@pytest.fixture(scope="module")
def other_key():
    return Key("key-2")


def jwks_answer(*keys):
    response = MagicMock(status_code=200)
    response.json.return_value = {"keys": [k.jwk() for k in keys]}
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def identity(key):
    """identity-service's JWKS endpoint, faked at httpx.get; no keys held before each test."""
    clear_key_cache()
    with patch(
        "genmo_fb_common.jwt_auth.httpx.get", return_value=jwks_answer(key)
    ) as fake:
        yield fake
    clear_key_cache()


def call(token=None):
    """Run the middleware; returns (request, response, the view was reached)."""
    view = MagicMock(return_value=MagicMock(status_code=200))
    extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
    request = RequestFactory().get("/", **extra)
    response = JwtAuthMiddleware(view)(request)
    return request, response, view.called


class TestAGenuineToken:
    def test_puts_the_caller_on_the_request(self, identity, key):
        request, response, reached = call(key.token())

        assert reached and response.status_code == 200
        assert request.bank_customer_id == "CUST-1"
        assert request.bank_id == "BANK-1"
        assert request.session_id == "session-1"
        assert request.token_claims["aud"] == AUDIENCE

    def test_needs_no_call_to_identity_per_request(self, identity, key):
        call(key.token())
        call(key.token())

        assert identity.call_count == 1  # the key set, once

    def test_fetches_the_key_set_from_identity_service(self, identity, key):
        with override_settings(IDENTITY_SERVICE_URL="http://identity:8001"):
            call(key.token())

        assert identity.call_args[0][0] == "http://identity:8001/api/v1/auth/jwks/"

    def test_allows_a_small_clock_difference(self, identity, key):
        just_expired = datetime.now(timezone.utc) - timedelta(seconds=5)

        _, response, reached = call(key.token(exp=just_expired))

        assert reached and response.status_code == 200


class TestNoToken:
    def test_goes_on_with_no_caller(self, identity):
        request, response, reached = call()

        assert reached and response.status_code == 200
        assert request.bank_customer_id is None
        assert request.bank_id is None
        assert request.session_id is None
        assert request.token_claims is None
        assert identity.call_count == 0


class TestARefusedToken:
    """FC-05: a tampered, expired or wrong-audience token gets 401."""

    def refused(self, token):
        request, response, reached = call(token)
        assert not reached
        assert response.status_code == 401
        assert json.loads(response.content)["error"]["code"] == "INVALID_TOKEN"
        assert response["WWW-Authenticate"] == "Bearer"
        assert request.bank_customer_id is None

    def test_tampered(self, identity, key):
        head, body, signature = key.token().split(".")
        other_body = key.token(sub="CUST-2").split(".")[1]

        self.refused(f"{head}.{other_body}.{signature}")

    def test_signed_by_somebody_else_under_the_same_key_id(self, identity):
        self.refused(Key("key-1").token())

    def test_expired(self, identity, key):
        long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)

        self.refused(key.token(exp=long_ago))

    def test_wrong_audience(self, identity, key):
        self.refused(key.token(aud="another-audience"))

    def test_wrong_issuer(self, identity, key):
        self.refused(key.token(iss="somebody-else"))

    def test_not_an_access_token(self, identity, key):
        self.refused(key.token(typ="refresh"))

    def test_without_a_session(self, identity, key):
        self.refused(key.token(sid=None))

    def test_without_a_subject(self, identity, key):
        self.refused(key.token(sub=None))

    def test_without_an_expiry(self, identity, key):
        self.refused(key.token(exp=None))

    def test_not_a_token_at_all(self, identity):
        self.refused("mock_token_CUST-1_BANK-1")

    def test_hs256_signed_with_the_public_key(self, identity, key):
        # the classic mix-up: the public key used as an HMAC secret
        head = jwt.utils.base64url_encode(
            json.dumps({"alg": "HS256", "typ": "JWT", "kid": key.kid}).encode()
        )
        body = key.token().split(".")[1].encode()
        signature = jwt.utils.base64url_encode(
            jwt.algorithms.HMACAlgorithm(jwt.algorithms.HMACAlgorithm.SHA256).sign(
                head + b"." + body, key.public_pem.encode()
            )
        )

        self.refused(b".".join([head, body, signature]).decode())

    def test_unsigned(self, identity, key):
        head = jwt.utils.base64url_encode(
            json.dumps({"alg": "none", "kid": key.kid}).encode()
        ).decode()
        body = key.token().split(".")[1]

        self.refused(f"{head}.{body}.")


class TestKeys:
    def test_a_new_key_is_picked_up(self, identity, key, other_key):
        call(key.token())
        identity.return_value = jwks_answer(key, other_key)

        with patch.object(jwt_auth, "MIN_REFETCH_SECONDS", 0):
            _, response, reached = call(other_key.token())

        assert reached and response.status_code == 200
        assert identity.call_count == 2

    def test_an_unknown_key_id_is_refused(self, identity, other_key):
        _, response, reached = call(other_key.token())

        assert not reached and response.status_code == 401

    def test_unknown_key_ids_do_not_flood_identity_service(self, identity, other_key):
        call(other_key.token())
        call(other_key.token())
        call(other_key.token())

        assert identity.call_count == 1

    def test_no_keys_and_identity_down_is_503(self, identity, key):
        identity.side_effect = httpx.ConnectError("down")

        request, response, reached = call(key.token())

        assert not reached
        assert response.status_code == 503
        assert json.loads(response.content)["error"]["code"] == "AUTH_UNAVAILABLE"
        assert request.bank_customer_id is None

    def test_identity_answering_badly_is_503(self, identity, key):
        identity.return_value = MagicMock(
            raise_for_status=MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=MagicMock()
                )
            )
        )

        _, response, reached = call(key.token())

        assert not reached and response.status_code == 503

    def test_held_keys_keep_working_while_identity_is_down(self, identity, key):
        call(key.token())
        identity.side_effect = httpx.ConnectError("down")

        with (
            override_settings(JWT_JWKS_CACHE_SECONDS=0),
            patch.object(jwt_auth, "MIN_REFETCH_SECONDS", 0),
        ):
            _, response, reached = call(key.token())

        assert reached and response.status_code == 200

    def test_keys_from_settings_need_no_fetch(self, identity, key):
        with override_settings(JWT_PUBLIC_KEYS={key.kid: key.public_pem}):
            request, response, reached = call(key.token())

        assert reached and response.status_code == 200
        assert request.bank_customer_id == "CUST-1"
        assert identity.call_count == 0

    def test_keys_from_settings_refuse_an_unknown_key_id(
        self, identity, key, other_key
    ):
        with override_settings(JWT_PUBLIC_KEYS={key.kid: key.public_pem}):
            _, response, reached = call(other_key.token())

        assert not reached and response.status_code == 401
        assert identity.call_count == 0


class TestVerifyAccessToken:
    def test_returns_the_claims(self, identity, key):
        claims = verify_access_token(key.token())

        assert claims["sub"] == "CUST-1" and claims["sid"] == "session-1"

    def test_raises_invalid_token(self, identity, key):
        with pytest.raises(InvalidTokenError):
            verify_access_token(key.token(aud="another-audience"))

    def test_raises_keys_unavailable(self, identity, key):
        identity.side_effect = httpx.ConnectError("down")

        with pytest.raises(KeysUnavailableError):
            verify_access_token(key.token())

    def test_issuer_and_audience_come_from_settings(self, identity, key):
        token = key.token(iss="bank-x-identity", aud="bank-x-services")

        with override_settings(
            JWT_ISSUER="bank-x-identity", JWT_AUDIENCE="bank-x-services"
        ):
            assert verify_access_token(token)["sub"] == "CUST-1"


class TestRetryWithNoKeys:
    def test_tries_again_soon_after_identity_was_down(self, identity, key):
        identity.side_effect = httpx.ConnectError("down")
        call(key.token())
        identity.side_effect = None

        _, response, _ = call(key.token())
        assert response.status_code == 503  # too soon to ask again

        with patch.object(jwt_auth, "NO_KEYS_RETRY_SECONDS", 0):
            _, response, reached = call(key.token())

        assert reached and response.status_code == 200
