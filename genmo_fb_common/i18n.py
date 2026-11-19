"""
Localisation (PL8 / XC-01 / NO-01): user-facing text by message key, in the bank's
languages (English, French, Arabic), in the language of the person it is for.

Each service keeps its texts in one catalog, loaded once when the service starts, so
finding a text is a dictionary lookup: no file, database or network on any request, and no
compile step that a build could forget (a forgotten `compilemessages` would silently leave
everything in English).

    # apps/core/messages.py
    from genmo_fb_common.i18n import Messages

    MESSAGES = Messages({
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
    })

    MESSAGES.text("goal.not_found")                    # in the request's language
    MESSAGES.text("payout.sent", language="fr", amount=format_amount(amount, language="fr"),
                  name="Aisha")

`MESSAGES.missing()` lists every key without all the bank's languages; a test that asserts
it is empty keeps every key in three languages (XC-01).

The language of an API request comes from its Accept-Language header (LanguageMiddleware),
limited to the languages the bank offers, otherwise the bank's default. Work done for
somebody who is not the caller (a notification) passes their language explicitly, or uses
`use_language(...)`.

    MIDDLEWARE = [..., "genmo_fb_common.i18n.LanguageMiddleware", ...]

The middleware also turns on Django's own translations (DRF's "This field is required."
and the like come in French and Arabic from Django itself) and answers with
`Content-Language`. Arabic is right to left: `is_rtl(language)`; laying it out is the
app's job.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Iterator, Optional

from django.utils import translation
from django.utils.cache import patch_vary_headers

from .bank_config import (
    bank_languages,
    default_language,
    is_rtl,
    resolve_language,
    to_bank_time,
)

__all__ = [
    "Messages",
    "LanguageMiddleware",
    "current_language",
    "use_language",
    "language_from_header",
    "format_date",
    "is_rtl",
]

_language: ContextVar[Optional[str]] = ContextVar("gfc_language", default=None)


# --------------------------------------------------------------------------- language


def language_from_header(header: Optional[str]) -> str:
    """
    The bank's language that fits an Accept-Language header best ("fr-FR,fr;q=0.9,en;q=0.5"
    gives "fr"), by the caller's order of preference; the bank's default when none fits.
    """
    offered = bank_languages()
    choices = []
    for position, part in enumerate((header or "").split(",")):
        code, _, params = part.strip().partition(";")
        if not code or code == "*":
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            choices.append((-quality, position, code))
    for _quality, _position, code in sorted(choices):
        base = code.replace("_", "-").split("-")[0].lower()
        if base in offered:
            return base
    return default_language()


def current_language() -> str:
    """The language of the work in hand: the request's, `use_language`'s, or the default."""
    return _language.get() or default_language()


@contextmanager
def use_language(language: Optional[str]) -> Iterator[str]:
    """Work in another person's language for a while (a notification, an event)."""
    resolved = resolve_language(language)
    token = _language.set(resolved)
    try:
        with translation.override(resolved):
            yield resolved
    finally:
        _language.reset(token)


class LanguageMiddleware:
    """
    Sets the request's language from Accept-Language (see `language_from_header`), as
    `request.LANGUAGE_CODE` and for `current_language()`; turns on Django's own
    translations for it; answers with Content-Language and Vary: Accept-Language.
    Put it after the request ID middleware and before anything that writes text.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = language_from_header(request.headers.get("Accept-Language"))
        request.LANGUAGE_CODE = language
        token = _language.set(language)
        translation.activate(language)
        try:
            response = self.get_response(request)
        finally:
            translation.deactivate()
            _language.reset(token)
        response.headers.setdefault("Content-Language", language)
        patch_vary_headers(response, ("Accept-Language",))
        return response


# --------------------------------------------------------------------------- messages


class Messages:
    """A service's user-facing texts by message key, each in the bank's languages."""

    def __init__(self, catalog: dict):
        self._catalog = catalog

    def keys(self) -> list:
        return list(self._catalog)

    def text(self, key: str, language: Optional[str] = None, **params) -> str:
        """
        The text for `key` in `language` (the work's language when none is given), with
        `params` filled in. A language the key lacks falls back to the bank's default,
        then English; an unknown key is a programming error (KeyError).
        """
        entry = self._catalog[key]
        chosen = resolve_language(language) if language else current_language()
        template = entry.get(chosen) or entry.get(default_language()) or entry["en"]
        return template.format(**params) if params else template

    def missing(self, languages: Optional[tuple] = None) -> list:
        """(key, language) for every key without a text in one of `languages`."""
        wanted = languages or bank_languages()
        return [
            (key, language)
            for key, entry in self._catalog.items()
            for language in wanted
            if not str(entry.get(language) or "").strip()
        ]

    def placeholder_mismatches(self) -> list:
        """(key, language) where a translation's {placeholders} differ from English's."""
        import string

        def names(text):
            return {name for _, name, _, _ in string.Formatter().parse(text) if name}

        found = []
        for key, entry in self._catalog.items():
            english = names(entry.get("en", ""))
            for language, text in entry.items():
                if names(text) != english:
                    found.append((key, language))
        return found


# --------------------------------------------------------------------------- dates

_MONTHS = {
    "en": (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ),
    "fr": (
        "janv.", "févr.", "mars", "avr.", "mai", "juin",
        "juil.", "août", "sept.", "oct.", "nov.", "déc.",
    ),
    "ar": (
        "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
        "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    ),
}  # fmt: skip


def format_date(value, language: Optional[str] = None, with_time: bool = False) -> str:
    """
    A date as people read it, on the bank's clock: "17 Nov 2026" in English,
    "17 nov. 2026" in French, "17 نوفمبر 2026" in Arabic (Western digits, as amounts),
    with ", 14:05" when `with_time`. A datetime is shown in the bank's time zone.
    """
    chosen = resolve_language(language) if language else current_language()
    months = _MONTHS.get(chosen, _MONTHS["en"])
    moment = to_bank_time(value) if isinstance(value, datetime) else value
    if not isinstance(moment, date):
        raise TypeError(f"Not a date: {value!r}")
    text = f"{moment.day} {months[moment.month - 1]} {moment.year}"
    if with_time and isinstance(moment, datetime):
        text += f", {moment:%H:%M}"
    return text
