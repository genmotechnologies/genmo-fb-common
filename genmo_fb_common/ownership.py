"""
Ownership helpers (FC-06 / PL5): a parent reaches only their own family's children, a child
only themselves.

Who the caller is comes from IdentitySessionMiddleware (`request.bank_customer_id`); their
family comes from family-service, asked with the caller's own token:

    GET {FAMILY_SERVICE_URL}/api/v1/families/

That one answer carries the caller's role and FA-06 permissions and every member of the family,
so every check below costs at most that one call per request (memoised on the request, cached
for 60 seconds per token). Asking about oneself needs no call at all.

Answers:
    403 NOT_YOUR_CHILD             somebody who is not the caller's own child
    403 FAMILY_PERMISSION_MISSING  a co-parent without the FA-06 permission for the action
    503 FAMILY_UNAVAILABLE         family-service could not be reached, so a child can't be
                                   confirmed (fail closed: nothing is shown)

Usage:
    from genmo_fb_common.ownership import ensure_self_or_own_child, ensure_parent_permission

    child = ensure_self_or_own_child(request, bank_customer_id=child_id)  # None when self
    ensure_parent_permission(request, "approve")
"""

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx
import structlog
from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.exceptions import APIException, NotAuthenticated, PermissionDenied

logger = structlog.get_logger(__name__)

FAMILY_PERMISSIONS = ("view_children", "approve", "limits_and_cards", "send_money")
CACHE_TTL = 60  # seconds, as the session cache in IdentitySessionMiddleware
REQUEST_TIMEOUT = 5.0
_REQUEST_ATTR = "_genmo_family_context"


class NotYourChild(PermissionDenied):
    default_detail = "This child is not in your family."
    default_code = "NOT_YOUR_CHILD"


class FamilyPermissionMissing(PermissionDenied):
    default_detail = "The family holder has not given you this permission."
    default_code = "FAMILY_PERMISSION_MISSING"


class FamilyUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Family details are unavailable right now. Please try again."
    default_code = "FAMILY_UNAVAILABLE"


@dataclass(frozen=True)
class FamilyMember:
    member_id: Optional[str]
    bank_customer_id: Optional[str]
    role: str  # "parent" or "child"
    permissions: Optional[dict] = None

    @property
    def is_child(self) -> bool:
        return self.role == "child"


@dataclass(frozen=True)
class FamilyContext:
    """The caller's family as family-service reports it; empty when they have none."""

    family_id: Optional[str] = None
    role: Optional[str] = None  # the caller's: "parent", "child" or None
    member_id: Optional[str] = None  # the caller's
    permissions: Optional[dict] = None  # the caller's FA-06 permissions
    members: tuple = field(default_factory=tuple)

    @property
    def is_parent(self) -> bool:
        return self.role == "parent"

    @property
    def is_child(self) -> bool:
        return self.role == "child"

    @property
    def children(self) -> list:
        return [m for m in self.members if m.is_child]

    def member(self, bank_customer_id=None, member_id=None) -> Optional[FamilyMember]:
        """The family member with this bank customer id or member id; None if not in the family."""
        for m in self.members:
            if (
                bank_customer_id is not None
                and m.bank_customer_id
                and str(m.bank_customer_id) == str(bank_customer_id)
            ):
                return m
            if (
                member_id is not None
                and m.member_id
                and str(m.member_id) == str(member_id)
            ):
                return m
        return None

    def own_child(
        self, bank_customer_id=None, member_id=None
    ) -> Optional[FamilyMember]:
        """That member when the caller is a parent and the member is a child of the family."""
        if not self.is_parent:
            return None
        found = self.member(bank_customer_id=bank_customer_id, member_id=member_id)
        return found if found is not None and found.is_child else None

    def may(self, name: str) -> bool:
        """
        FA-06: may the caller do `name` (approve, limits_and_cards, send_money) for the children?
        Only a parent; the holder always, a co-parent only with the permission the holder gave.
        A family-service that reports no permissions predates FA-06, when every parent could do
        everything (chores-service treats it the same way).
        """
        if not self.is_parent:
            return False
        if self.permissions is None:
            return True
        return bool(self.permissions.get(name))

    # cache storage: plain data only
    def to_cache(self) -> dict:
        return {
            "family_id": self.family_id,
            "role": self.role,
            "member_id": self.member_id,
            "permissions": self.permissions,
            "members": [asdict(m) for m in self.members],
        }

    @classmethod
    def from_cache(cls, data: dict) -> "FamilyContext":
        return cls(
            family_id=data.get("family_id"),
            role=data.get("role"),
            member_id=data.get("member_id"),
            permissions=data.get("permissions"),
            members=tuple(FamilyMember(**m) for m in data.get("members") or []),
        )


EMPTY = FamilyContext()


def bearer_token(request) -> Optional[str]:
    header = (
        request.headers.get("Authorization", "") if hasattr(request, "headers") else ""
    )
    return header[7:] if header.startswith("Bearer ") else None


def _parse(answer, caller_bank_customer_id) -> FamilyContext:
    families = answer.get("results", []) if isinstance(answer, dict) else answer or []
    family = next((f for f in families if f.get("my_role")), None)
    if family is None:
        return EMPTY
    members = tuple(
        FamilyMember(
            member_id=str(m["id"]) if m.get("id") else None,
            bank_customer_id=(
                str(m["bank_customer_id"]) if m.get("bank_customer_id") else None
            ),
            role=str(m.get("role") or "").lower(),
            permissions=m.get("permissions"),
        )
        for m in family.get("members") or []
    )
    me = next(
        (m for m in members if m.bank_customer_id == str(caller_bank_customer_id)), None
    )
    return FamilyContext(
        family_id=str(family["id"]) if family.get("id") else None,
        role=str(family["my_role"]).lower(),
        member_id=me.member_id if me else None,
        permissions=family.get("my_permissions"),
        members=members,
    )


def fetch_family_context(token: str, caller_bank_customer_id: str) -> FamilyContext:
    """
    The caller's family from family-service (one call, cached 60 s per token).
    A refused token or no family is an empty context; family-service unreachable or failing
    raises FamilyUnavailable (503) and is not cached.
    """
    cache_key = (
        f"genmo_family_context:{hashlib.sha256(token.encode()).hexdigest()[:32]}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return FamilyContext.from_cache(cached)

    base_url = getattr(settings, "FAMILY_SERVICE_URL", "http://family-service:8002")
    try:
        response = httpx.get(
            f"{base_url}/api/v1/families/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("family_service_unavailable", error=str(exc))
        raise FamilyUnavailable() from exc

    if response.status_code in (401, 403):
        context = EMPTY  # family-service does not accept this caller: no family access
    elif response.status_code != 200:
        logger.warning("family_service_failed", status=response.status_code)
        raise FamilyUnavailable()
    else:
        context = _parse(response.json(), caller_bank_customer_id)

    cache.set(cache_key, context.to_cache(), CACHE_TTL)
    return context


def get_family_context(request) -> FamilyContext:
    """The caller's family, asked once per request; empty without a signed-in caller."""
    raw = getattr(request, "_request", request)  # DRF Request wraps the Django one
    cached = getattr(raw, _REQUEST_ATTR, None)
    if cached is not None:
        return cached
    caller = getattr(request, "bank_customer_id", None)
    token = bearer_token(request)
    context = fetch_family_context(token, caller) if caller and token else EMPTY
    setattr(raw, _REQUEST_ATTR, context)
    return context


def ensure_self_or_own_child(
    request, bank_customer_id=None, member_id=None
) -> Optional[FamilyMember]:
    """
    None when the caller asks about themselves (by bank customer id, no family-service call);
    the child when the caller is a parent and it is a child of their own family; otherwise
    NotYourChild (403). Without a signed-in caller: NotAuthenticated (401).
    """
    if bank_customer_id is None and member_id is None:
        raise ValueError("Give bank_customer_id or member_id.")
    caller = getattr(request, "bank_customer_id", None)
    if not caller:
        raise NotAuthenticated()
    if bank_customer_id is not None and str(bank_customer_id) == str(caller):
        return None
    context = get_family_context(request)
    if (
        member_id is not None
        and context.member_id
        and str(member_id) == str(context.member_id)
    ):
        return None
    child = context.own_child(bank_customer_id=bank_customer_id, member_id=member_id)
    if child is None:
        raise NotYourChild()
    return child


def ensure_parent_permission(request, name: str) -> FamilyContext:
    """
    FA-06: the caller is a parent with the `name` permission (approve, limits_and_cards,
    send_money). A child or a non-member gets PermissionDenied, a co-parent without it
    FamilyPermissionMissing (both 403). Returns the family context for further checks.
    """
    if name not in FAMILY_PERMISSIONS:
        raise ValueError(f"Unknown family permission: {name}")
    if not getattr(request, "bank_customer_id", None):
        raise NotAuthenticated()
    context = get_family_context(request)
    if not context.is_parent:
        raise PermissionDenied("Only parents can perform this action.")
    if not context.may(name):
        raise FamilyPermissionMissing()
    return context
