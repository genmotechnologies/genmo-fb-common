"""FC-06: the DRF permission classes use the family from family-service (see test_ownership)."""

from types import SimpleNamespace

import httpx
import pytest
from django.test import RequestFactory
from rest_framework.response import Response
from rest_framework.views import APIView

from genmo_fb_common.ownership import NotYourChild
from genmo_fb_common.permissions import (
    IsChild,
    IsFamilyMember,
    IsOwnerOrParent,
    IsParent,
    IsParentOrReadOnly,
    IsSelfOrOwnChild,
)
from tests.family_fake import request_as

pytestmark = pytest.mark.usefixtures("family_service")

VIEW = SimpleNamespace(kwargs={})


def wallet(owner):
    return SimpleNamespace(bank_customer_id=owner)


class TestRoleClasses:
    @pytest.mark.parametrize(
        "who,parent,child",
        [
            ("RASHID", True, False),
            ("SAMINA", True, False),
            ("AISHA", False, True),
            ("STRANGER", False, False),
        ],
    )
    def test_the_role_comes_from_family_service(self, who, parent, child):
        assert IsParent().has_permission(request_as(who), VIEW) is parent
        assert IsChild().has_permission(request_as(who), VIEW) is child

    def test_signed_out_is_refused_without_a_call(self, family_service):
        raw = RequestFactory().get("/")
        raw.bank_customer_id = None
        assert not IsParent().has_permission(raw, VIEW)
        assert not IsParentOrReadOnly().has_permission(raw, VIEW)
        assert family_service.call_count == 0

    def test_children_read_parents_write(self, family_service):
        reading = request_as("AISHA")
        assert IsParentOrReadOnly().has_permission(reading, VIEW)
        assert family_service.call_count == 0  # reading needs no role
        writing = RequestFactory().post("/", HTTP_AUTHORIZATION="Bearer token-AISHA")
        writing.bank_customer_id = "AISHA"
        assert not IsParentOrReadOnly().has_permission(writing, VIEW)
        writing = RequestFactory().post("/", HTTP_AUTHORIZATION="Bearer token-RASHID")
        writing.bank_customer_id = "RASHID"
        assert IsParentOrReadOnly().has_permission(writing, VIEW)


class TestIsFamilyMember:
    def test_own_family_only(self):
        assert IsFamilyMember().has_permission(
            request_as("AISHA"), SimpleNamespace(kwargs={"family_id": "fam-a"})
        )
        assert not IsFamilyMember().has_permission(
            request_as("RASHID"), SimpleNamespace(kwargs={"family_id": "fam-b"})
        )

    def test_from_get_family_id(self):
        view = SimpleNamespace(kwargs={}, get_family_id=lambda: "fam-b")
        assert IsFamilyMember().has_permission(request_as("SANA"), view)

    def test_no_family_id_or_no_family(self):
        assert not IsFamilyMember().has_permission(request_as("RASHID"), VIEW)
        assert not IsFamilyMember().has_permission(
            request_as("STRANGER"), SimpleNamespace(kwargs={"family_id": "fam-a"})
        )


class TestObjectOwnership:
    @pytest.mark.parametrize("permission", [IsSelfOrOwnChild, IsOwnerOrParent])
    def test_owner_and_own_parent_pass(self, permission):
        assert permission().has_object_permission(
            request_as("AISHA"), VIEW, wallet("AISHA")
        )
        assert permission().has_object_permission(
            request_as("SAMINA"), VIEW, wallet("AISHA")
        )

    @pytest.mark.parametrize("permission", [IsSelfOrOwnChild, IsOwnerOrParent])
    def test_acceptance_another_familys_parent_is_403(self, permission):
        with pytest.raises(NotYourChild):
            permission().has_object_permission(
                request_as("SANA"), VIEW, wallet("AISHA")
            )

    def test_the_view_may_name_the_owner(self):
        view = SimpleNamespace(
            kwargs={}, get_owner_bank_customer_id=lambda obj: obj.child
        )
        assert IsSelfOrOwnChild().has_object_permission(
            request_as("RASHID"), view, SimpleNamespace(child="SARA")
        )

    def test_an_object_without_an_owner_is_refused(self):
        assert not IsSelfOrOwnChild().has_object_permission(
            request_as("RASHID"), VIEW, SimpleNamespace()
        )

    def test_is_owner_or_parent_falls_back_to_created_by_id(self):
        goal = SimpleNamespace(bank_customer_id=None, created_by_id="SARA")
        assert IsOwnerOrParent().has_object_permission(request_as("RASHID"), VIEW, goal)
        with pytest.raises(NotYourChild):
            IsOwnerOrParent().has_object_permission(request_as("SANA"), VIEW, goal)


class WalletView(APIView):
    authentication_classes = []
    permission_classes = [IsSelfOrOwnChild]

    def get(self, request, owner):
        self.check_object_permissions(request, wallet(owner))
        return Response({"owner": owner})


class TestInAView:
    def call(self, who, owner):
        raw = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer token-{who}")
        raw.bank_customer_id = who
        return WalletView.as_view()(raw, owner=owner)

    def test_own_child_200(self):
        assert self.call("RASHID", "SARA").status_code == 200

    def test_another_familys_child_403_not_your_child(self):
        response = self.call("RASHID", "BILAL")
        assert response.status_code == 403
        assert response.data["detail"].code == "NOT_YOUR_CHILD"

    def test_family_service_down_503(self, family_service):
        family_service.side_effect = httpx.ConnectError("down")
        assert self.call("RASHID", "SARA").status_code == 503
