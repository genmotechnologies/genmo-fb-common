"""FC-06 ownership helpers, against the fake family-service in tests/family_fake.py."""

from unittest.mock import MagicMock

import httpx
import pytest
from django.core.cache import cache
from django.test import RequestFactory
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from genmo_fb_common.ownership import (
    EMPTY,
    FamilyContext,
    FamilyPermissionMissing,
    FamilyUnavailable,
    NotYourChild,
    ensure_parent_permission,
    ensure_self_or_own_child,
    fetch_family_context,
    get_family_context,
)
from tests.family_fake import APPROVE_ONLY, FAMILY_A, answer_for, request_as

pytestmark = pytest.mark.usefixtures("family_service")


class TestTheFamilyContext:
    def test_one_answer_gives_role_permissions_and_members(self):
        context = get_family_context(request_as("SAMINA"))
        assert (context.family_id, context.role, context.member_id) == (
            "fam-a",
            "parent",
            "m-samina",
        )
        assert context.permissions == APPROVE_ONLY
        assert [c.bank_customer_id for c in context.children] == ["AISHA", "SARA", None]

    def test_asked_once_per_request(self, family_service):
        request = request_as("RASHID")
        get_family_context(request)
        cache.clear()  # only the request's own memo may answer now
        get_family_context(request)
        ensure_self_or_own_child(request, bank_customer_id="AISHA")
        assert family_service.call_count == 1

    def test_the_drf_and_django_request_share_one_answer(self, family_service):
        drf = request_as("RASHID")
        get_family_context(drf)
        cache.clear()
        get_family_context(drf._request)
        assert family_service.call_count == 1

    def test_cached_60_seconds_across_requests(self, family_service):
        get_family_context(request_as("RASHID"))
        context = get_family_context(request_as("RASHID"))
        assert family_service.call_count == 1
        assert context.own_child(bank_customer_id="SARA").member_id == "m-sara"

    def test_a_caller_without_a_family_has_an_empty_context(self):
        assert get_family_context(request_as("STRANGER")) == EMPTY

    def test_no_signed_in_caller_means_no_call(self, family_service):
        raw = RequestFactory().get("/")
        raw.bank_customer_id = None
        assert get_family_context(raw) == EMPTY
        assert family_service.call_count == 0

    def test_a_refused_token_is_an_empty_context(self, family_service):
        family_service.side_effect = None
        family_service.return_value = MagicMock(status_code=401)
        assert fetch_family_context("bad", "RASHID") == EMPTY

    def test_family_service_down_is_503_and_not_cached(self, family_service):
        family_service.side_effect = httpx.ConnectError("down")
        with pytest.raises(FamilyUnavailable) as raised:
            fetch_family_context("token-RASHID", "RASHID")
        assert (
            raised.value.status_code == 503
            and raised.value.default_code == "FAMILY_UNAVAILABLE"
        )
        family_service.side_effect = lambda url, headers, timeout: answer_for(
            headers["Authorization"][7:]
        )
        assert fetch_family_context(
            "token-RASHID", "RASHID"
        ).is_parent  # asked again, not cached

    def test_family_service_failing_is_503(self, family_service):
        family_service.side_effect = None
        family_service.return_value = MagicMock(status_code=500)
        with pytest.raises(FamilyUnavailable):
            fetch_family_context("token-RASHID", "RASHID")

    def test_a_family_the_caller_is_not_in_is_skipped(self, family_service):
        response = MagicMock(status_code=200)
        response.json.return_value = [
            {"id": "fam-x", "my_role": None, "members": []},
            {"id": "fam-a", "my_role": "CHILD", "members": FAMILY_A["members"]},
        ]
        family_service.side_effect = None
        family_service.return_value = response
        context = fetch_family_context("token-AISHA", "AISHA")
        assert (
            context.family_id == "fam-a"
            and context.is_child
            and context.member_id == "m-aisha"
        )


class TestSelfOrOwnChild:
    def test_oneself_needs_no_family_call(self, family_service):
        assert (
            ensure_self_or_own_child(request_as("AISHA"), bank_customer_id="AISHA")
            is None
        )
        assert family_service.call_count == 0

    def test_oneself_by_member_id(self):
        assert (
            ensure_self_or_own_child(request_as("AISHA"), member_id="m-aisha") is None
        )

    def test_a_parent_reaches_their_own_child_by_bank_or_member_id(self):
        assert (
            ensure_self_or_own_child(
                request_as("RASHID"), bank_customer_id="AISHA"
            ).member_id
            == "m-aisha"
        )
        assert (
            ensure_self_or_own_child(
                request_as("SAMINA"), member_id="m-sara"
            ).bank_customer_id
            == "SARA"
        )

    def test_acceptance_parent_a_asking_for_parent_bs_child_is_403(self):
        with pytest.raises(NotYourChild) as raised:
            ensure_self_or_own_child(request_as("RASHID"), bank_customer_id="BILAL")
        assert (
            raised.value.status_code == 403
            and raised.value.default_code == "NOT_YOUR_CHILD"
        )

    def test_another_familys_member_id_is_403(self):
        with pytest.raises(NotYourChild):
            ensure_self_or_own_child(request_as("RASHID"), member_id="m-bilal")

    def test_a_co_parent_is_not_a_child(self):
        with pytest.raises(NotYourChild):
            ensure_self_or_own_child(request_as("RASHID"), bank_customer_id="SAMINA")

    def test_a_child_does_not_reach_a_sibling(self):
        with pytest.raises(NotYourChild):
            ensure_self_or_own_child(request_as("AISHA"), bank_customer_id="SARA")

    def test_a_missing_bank_id_never_matches(self):
        with pytest.raises(NotYourChild):
            ensure_self_or_own_child(request_as("RASHID"), bank_customer_id="None")

    def test_no_signed_in_caller_is_401(self):
        raw = RequestFactory().get("/")
        raw.bank_customer_id = None
        with pytest.raises(NotAuthenticated):
            ensure_self_or_own_child(raw, bank_customer_id="AISHA")

    def test_family_service_down_shows_no_child(self, family_service):
        family_service.side_effect = httpx.ConnectError("down")
        with pytest.raises(FamilyUnavailable):
            ensure_self_or_own_child(request_as("RASHID"), bank_customer_id="AISHA")

    def test_needs_an_id(self):
        with pytest.raises(ValueError):
            ensure_self_or_own_child(request_as("RASHID"))


class TestParentPermission:
    def test_the_holder_may_do_everything(self):
        request = request_as("RASHID")
        for name in ("approve", "limits_and_cards", "send_money"):
            assert ensure_parent_permission(request, name).is_parent

    def test_a_co_parent_with_the_permission(self):
        assert (
            ensure_parent_permission(request_as("SAMINA"), "approve").family_id
            == "fam-a"
        )

    def test_a_co_parent_without_it_is_403_family_permission_missing(self):
        with pytest.raises(FamilyPermissionMissing) as raised:
            ensure_parent_permission(request_as("SAMINA"), "send_money")
        assert raised.value.default_code == "FAMILY_PERMISSION_MISSING"

    def test_a_child_is_403(self):
        with pytest.raises(PermissionDenied) as raised:
            ensure_parent_permission(request_as("AISHA"), "approve")
        assert not isinstance(raised.value, FamilyPermissionMissing)

    def test_no_family_is_403(self):
        with pytest.raises(PermissionDenied):
            ensure_parent_permission(request_as("STRANGER"), "approve")

    def test_a_family_service_without_permissions_lets_a_parent_through(
        self, family_service
    ):
        response = answer_for("token-SAMINA")
        body = response.json.return_value
        body["results"][0]["my_permissions"] = None
        family_service.side_effect = None
        family_service.return_value = response
        assert ensure_parent_permission(request_as("SAMINA"), "send_money").is_parent

    def test_a_child_may_do_nothing_even_without_reported_permissions(self):
        assert FamilyContext(role="child", permissions=None).may("approve") is False
        assert FamilyContext(role="parent", permissions=None).may("approve") is True

    def test_unknown_permission_is_a_programming_error(self):
        with pytest.raises(ValueError):
            ensure_parent_permission(request_as("RASHID"), "fly")

    def test_no_signed_in_caller_is_401(self):
        raw = RequestFactory().get("/")
        raw.bank_customer_id = None
        with pytest.raises(NotAuthenticated):
            ensure_parent_permission(raw, "approve")
