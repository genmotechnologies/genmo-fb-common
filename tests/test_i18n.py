"""
COM-032 (PL8 / XC-01 / NO-01): user-facing text by message key in the bank's languages,
in the caller's language, from memory (no lookup per request).
"""

from datetime import date, datetime, timezone

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from rest_framework import serializers

from genmo_fb_common.i18n import (
    LanguageMiddleware,
    Messages,
    current_language,
    format_date,
    is_rtl,
    language_from_header,
    use_language,
)

MESSAGES = Messages(
    {
        "goal.not_found": {
            "en": "This goal does not exist.",
            "fr": "Cet objectif n'existe pas.",
            "ar": "هذا الهدف غير موجود.",
        },
        "payout.sent": {
            "en": "{amount} sent to {name}.",
            "fr": "{amount} envoyé à {name}.",
            "ar": "تم إرسال {amount} إلى {name}.",
        },
    }
)


class TestTheLanguageOfARequest:
    @pytest.mark.parametrize(
        "header, expected",
        [
            ("fr-FR,fr;q=0.9,en;q=0.5", "fr"),
            ("ar", "ar"),
            ("en-US", "en"),
            ("de-DE,ar;q=0.8", "ar"),  # German isn't offered: the next choice
            ("en;q=0.2,fr;q=0.9", "fr"),  # by quality, not by place
            ("de", "en"),  # nothing offered: the bank's default
            ("", "en"),
            (None, "en"),
            ("fr;q=0", "en"),  # q=0 means "not this one"
            ("*", "en"),
        ],
    )
    def test_from_accept_language(self, header, expected):
        assert language_from_header(header) == expected

    @override_settings(BANK_DEFAULT_LANGUAGE="fr")
    def test_the_banks_default_when_nothing_fits(self):
        assert language_from_header("de") == "fr"

    @override_settings(BANK_LANGUAGES=("fr", "ar"), BANK_DEFAULT_LANGUAGE="fr")
    def test_only_the_languages_the_bank_offers(self):
        assert language_from_header("en") == "fr"


def call(header=None, view=None):
    seen = {}

    def default_view(request):
        seen["language"] = current_language()
        seen["request"] = request.LANGUAGE_CODE
        return HttpResponse(MESSAGES.text("goal.not_found"))

    extra = {"HTTP_ACCEPT_LANGUAGE": header} if header else {}
    response = LanguageMiddleware(view or default_view)(
        RequestFactory().get("/", **extra)
    )
    return response, seen


class TestTheMiddleware:
    def test_acceptance_a_french_caller_gets_french(self):
        response, seen = call("fr-FR,fr;q=0.9")

        assert response.content.decode() == "Cet objectif n'existe pas."
        assert seen == {"language": "fr", "request": "fr"}
        assert response["Content-Language"] == "fr"
        assert "Accept-Language" in response["Vary"]

    def test_acceptance_an_arabic_caller_gets_arabic_right_to_left(self):
        response, _ = call("ar")

        assert response.content.decode() == "هذا الهدف غير موجود."
        assert response["Content-Language"] == "ar"
        assert is_rtl("ar") and not is_rtl("fr")

    def test_no_header_gets_the_banks_default(self):
        response, _ = call()

        assert response.content.decode() == "This goal does not exist."

    def test_djangos_own_messages_come_translated(self):
        class Form(serializers.Serializer):
            name = serializers.CharField()

        def view(request):
            form = Form(data={})
            form.is_valid()
            return HttpResponse(str(form.errors["name"][0]))

        response, _ = call("fr", view=view)

        assert response.content.decode() == "Ce champ est obligatoire."

    def test_the_language_does_not_leak_into_the_next_request(self):
        call("ar")

        assert current_language() == "en"


class TestMessages:
    def test_a_text_with_its_values(self):
        assert MESSAGES.text("payout.sent", "fr", amount="50,00 MRU", name="Aisha") == (
            "50,00 MRU envoyé à Aisha."
        )

    def test_for_somebody_else_in_their_language(self):
        with use_language("ar"):
            assert MESSAGES.text("goal.not_found") == "هذا الهدف غير موجود."
        assert current_language() == "en"

    def test_a_missing_translation_falls_back_to_the_default_then_english(self):
        partial = Messages({"k": {"en": "Hello"}})

        assert partial.text("k", "fr") == "Hello"

    def test_an_unknown_key_is_a_programming_error(self):
        with pytest.raises(KeyError):
            MESSAGES.text("no.such.key")

    def test_acceptance_missing_lists_every_key_without_all_three(self):
        partial = Messages({"a": {"en": "A", "fr": "A", "ar": "أ"}, "b": {"en": "B"}})

        assert MESSAGES.missing() == []
        assert partial.missing() == [("b", "fr"), ("b", "ar")]

    def test_placeholders_must_match_english(self):
        wrong = Messages(
            {"k": {"en": "{amount} to {name}", "fr": "{montant} à {name}"}}
        )

        assert MESSAGES.placeholder_mismatches() == []
        assert wrong.placeholder_mismatches() == [("k", "fr")]


class TestDates:
    @pytest.mark.parametrize(
        "language, expected",
        [
            ("en", "17 Nov 2026"),
            ("fr", "17 nov. 2026"),
            ("ar", "17 نوفمبر 2026"),
        ],
    )
    def test_a_date_in_each_language(self, language, expected):
        assert format_date(date(2026, 11, 17), language) == expected

    @override_settings(BANK_TIMEZONE="Asia/Karachi")
    def test_a_moment_is_shown_on_the_banks_clock(self):
        moment = datetime(2026, 11, 17, 20, 30, tzinfo=timezone.utc)  # 01:30 next day

        assert format_date(moment, "en", with_time=True) == "18 Nov 2026, 01:30"

    def test_not_a_date(self):
        with pytest.raises(TypeError):
            format_date("2026-11-17", "en")
