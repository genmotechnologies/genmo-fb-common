"""
COM-032 (PL8 / XC-01): genmo-fb-common's own refusals come in the caller's language.
"""

import pytest

from genmo_fb_common.i18n import use_language
from genmo_fb_common.messages import MESSAGES
from genmo_fb_common.ownership import FamilyPermissionMissing, NotYourChild
from genmo_fb_common.permissions import IsChild, IsParent


def test_every_text_in_every_language_with_the_same_placeholders():
    assert MESSAGES.missing() == []
    assert MESSAGES.placeholder_mismatches() == []


@pytest.mark.parametrize(
    "language, text",
    [
        ("en", "This child is not in your family."),
        ("fr", "Cet enfant ne fait pas partie de votre famille."),
        ("ar", "هذا الطفل ليس من عائلتك."),
    ],
)
def test_acceptance_not_your_child_in_the_language_it_is_raised_in(language, text):
    with use_language(language):
        error = NotYourChild()

    assert str(error.detail) == text
    assert error.detail.code == "NOT_YOUR_CHILD"  # the code does not change


def test_a_permission_message_follows_the_language():
    with use_language("fr"):
        assert IsParent().message == "Seuls les parents peuvent effectuer cette action."
        assert FamilyPermissionMissing().detail.code == "FAMILY_PERMISSION_MISSING"
    assert IsChild().message == "Only children can perform this action."


def test_a_given_detail_is_kept():
    assert str(NotYourChild("custom").detail) == "custom"
