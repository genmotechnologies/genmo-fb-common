"""
A fake family-service for the FC-06 tests. Family A: RASHID (holder), SAMINA (co-parent,
approve only), AISHA and SARA (children). Family B: SANA (parent), BILAL (child). Each token
"token-<id>" gets the families of that user, as the real GET /api/v1/families/ answers.
"""

from unittest.mock import MagicMock

from django.test import RequestFactory
from rest_framework.request import Request

ALL = {
    "view_children": True,
    "approve": True,
    "limits_and_cards": True,
    "send_money": True,
}
APPROVE_ONLY = {
    "view_children": True,
    "approve": True,
    "limits_and_cards": False,
    "send_money": False,
}
NONE = {name: False for name in ALL}


def member(mid, bank_id, role, permissions):
    return {
        "id": mid,
        "bank_customer_id": bank_id,
        "role": role,
        "permissions": permissions,
        "family_role": "holder",
        "first_name": "x",
    }


FAMILY_A = {
    "id": "fam-a",
    "name": "A",
    "members": [
        member("m-rashid", "RASHID", "parent", ALL),
        member("m-samina", "SAMINA", "parent", APPROVE_ONLY),
        member("m-aisha", "AISHA", "child", NONE),
        member("m-sara", "SARA", "child", NONE),
        member("m-invited", None, "child", NONE),  # invited child without a bank id yet
    ],
}
FAMILY_B = {
    "id": "fam-b",
    "name": "B",
    "members": [
        member("m-sana", "SANA", "parent", ALL),
        member("m-bilal", "BILAL", "child", NONE),
    ],
}
WHERE = {
    "RASHID": FAMILY_A,
    "SAMINA": FAMILY_A,
    "AISHA": FAMILY_A,
    "SARA": FAMILY_A,
    "SANA": FAMILY_B,
    "BILAL": FAMILY_B,
}


def answer_for(token):
    """family-service: the token's user's families, with my_role and my_permissions."""
    who = token.removeprefix("token-")
    family = WHERE.get(who)
    response = MagicMock(status_code=200)
    if family is None:
        response.json.return_value = {"results": []}
        return response
    me = next(m for m in family["members"] if m["bank_customer_id"] == who)
    response.json.return_value = {
        "count": 1,
        "results": [
            {**family, "my_role": me["role"], "my_permissions": me["permissions"]}
        ],
    }
    return response


def request_as(who, drf=True):
    raw = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer token-{who}")
    raw.bank_customer_id = who  # what IdentitySessionMiddleware sets
    return Request(raw) if drf else raw
