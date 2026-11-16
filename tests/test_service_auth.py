"""
COM-030 (PL6 / FC-07): "Service token client: fetches and caches a service token for
scheduled jobs and internal calls. A scheduled payout runs with a valid service token; a
call without one gets 401."
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.test import RequestFactory, override_settings
from rest_framework.response import Response
from rest_framework.views import APIView

from genmo_fb_common import service_auth
from genmo_fb_common.jwt_auth import InvalidTokenError, JwtAuthMiddleware
from genmo_fb_common.service_auth import (
    HasServiceScope,
    ServiceTokenAuthentication,
    ServiceTokenUnavailableError,
    clear_service_token_cache,
    get_service_token,
    verify_service_token,
)
from tests.test_jwt_auth import Key

CREDENTIALS = {"SERVICE_CLIENT_ID": "pocket-money", "SERVICE_CLIENT_SECRET": "s3cret"}


@pytest.fixture(scope="module")
def key():
    return Key("key-1")


@pytest.fixture(autouse=True)
def public_key(key, settings):
    settings.JWT_PUBLIC_KEYS = {key.kid: key.public_pem}
    clear_service_token_cache()
    yield
    clear_service_token_cache()


def service_token(key, scope="transfers:scheduled", **changes):
    claims = {
        "sub": "service:pocket-money",
        "typ": "service",
        "scope": scope,
        "sid": None,
        "bank_id": None,
        "jti": None,
    }
    claims.update(changes)
    return key.token(**claims)


def identity_answer(token="tok-1", expires_in=300, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }
    return response


class TestGettingAToken:
    @override_settings(IDENTITY_SERVICE_URL="http://identity:8001", **CREDENTIALS)
    def test_asks_identity_service_with_this_services_credentials(self):
        with patch.object(
            service_auth.httpx, "post", return_value=identity_answer()
        ) as post:
            assert get_service_token() == "tok-1"

        url = post.call_args[0][0]
        assert url == "http://identity:8001/api/v1/auth/service-token/"
        assert post.call_args[1]["data"] == {
            "grant_type": "client_credentials",
            "client_id": "pocket-money",
            "client_secret": "s3cret",
        }

    @override_settings(**CREDENTIALS)
    def test_is_kept_and_not_asked_for_again(self):
        with patch.object(
            service_auth.httpx, "post", return_value=identity_answer()
        ) as post:
            get_service_token()
            get_service_token()

        assert post.call_count == 1

    @override_settings(**CREDENTIALS)
    def test_is_fetched_again_shortly_before_it_expires(self):
        answers = [identity_answer("old", expires_in=30), identity_answer("new")]
        with patch.object(service_auth.httpx, "post", side_effect=answers):
            assert get_service_token() == "old"
            assert get_service_token() == "new"  # 30 s left is under the 60 s margin

    def test_without_credentials_there_is_none(self):
        with pytest.raises(ServiceTokenUnavailableError):
            get_service_token()

    @override_settings(**CREDENTIALS)
    def test_refused_credentials(self):
        with patch.object(
            service_auth.httpx, "post", return_value=identity_answer(status=401)
        ):
            with pytest.raises(ServiceTokenUnavailableError):
                get_service_token()

    @override_settings(**CREDENTIALS)
    def test_identity_down(self):
        with patch.object(
            service_auth.httpx, "post", side_effect=httpx.ConnectError("down")
        ):
            with pytest.raises(ServiceTokenUnavailableError):
                get_service_token()


class TestCheckingAToken:
    def test_a_service_token_is_genuine(self, key):
        claims = verify_service_token(service_token(key))

        assert claims["sub"] == "service:pocket-money"

    def test_a_users_access_token_is_not_a_service_token(self, key):
        with pytest.raises(InvalidTokenError):
            verify_service_token(key.token())

    def test_expired(self, key):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(InvalidTokenError):
            verify_service_token(service_token(key, exp=past))

    def test_wrong_audience(self, key):
        with pytest.raises(InvalidTokenError):
            verify_service_token(service_token(key, aud="elsewhere"))

    def test_signed_by_another_key(self, key):
        other = Key("key-1")  # same key id, other key
        with pytest.raises(InvalidTokenError):
            verify_service_token(service_token(other))

    def test_a_service_token_is_refused_where_a_user_is_expected(self, key):
        """ID-04: a service token can't be used to act as a parent or child."""
        from genmo_fb_common.jwt_auth import verify_access_token

        with pytest.raises(InvalidTokenError):
            verify_access_token(service_token(key))


class InternalView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [HasServiceScope]
    required_scope = "transfers:scheduled"

    def post(self, request):
        return Response({"caller": request.user.name})


class NoScopeView(InternalView):
    required_scope = None


def call(view, token=None):
    extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    request = RequestFactory().post("/api/v1/internal/transfers/", **extra)
    return view.as_view()(request)


class TestAnInternalEndpoint:
    def test_acceptance_a_scheduled_job_with_a_valid_service_token_gets_through(
        self, key
    ):
        response = call(InternalView, service_token(key))

        assert response.status_code == 200
        assert response.data == {"caller": "pocket-money"}

    def test_acceptance_a_call_without_one_gets_401(self):
        assert call(InternalView).status_code == 401

    def test_a_parents_token_gets_401(self, key):
        assert call(InternalView, key.token()).status_code == 401

    def test_a_service_token_without_the_scope_gets_403(self, key):
        assert (
            call(InternalView, service_token(key, scope="family:read")).status_code
            == 403
        )

    def test_a_view_that_names_no_scope_lets_nobody_in(self, key):
        assert call(NoScopeView, service_token(key)).status_code == 403


class TestTheUserMiddleware:
    def test_leaves_internal_paths_to_the_service_check(self, key):
        view = MagicMock(return_value=MagicMock(status_code=200))
        request = RequestFactory().post(
            "/api/v1/internal/transfers/",
            HTTP_AUTHORIZATION=f"Bearer {service_token(key)}",
        )

        JwtAuthMiddleware(view)(request)

        assert view.called and request.bank_customer_id is None

    def test_still_refuses_a_service_token_on_a_user_endpoint(self, key):
        view = MagicMock(return_value=MagicMock(status_code=200))
        request = RequestFactory().post(
            "/api/v1/transfers/", HTTP_AUTHORIZATION=f"Bearer {service_token(key)}"
        )

        response = JwtAuthMiddleware(view)(request)

        assert not view.called and response.status_code == 401
