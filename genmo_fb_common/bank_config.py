"""
Bank configuration (FC-09 / PL8): the bank's currency, amounts, time zone and languages come
from settings, never from code. No hard-coded PKR, SAR or Asia/Karachi anywhere.

Settings (all optional):
    BANK_CURRENCY           ISO 4217 code (default "MRU")
    BANK_CURRENCY_DECIMALS  decimals of that currency (default: the ISO number for the
                            currencies listed below, otherwise 2)
    BANK_TIMEZONE           IANA name (default "Africa/Nouakchott")
    BANK_WEEK_START         first day of the week, 0 Monday to 6 Sunday (default 0)
    BANK_LANGUAGES          languages the bank offers (default ("en", "fr", "ar"))
    BANK_DEFAULT_LANGUAGE   used when a caller's language is not offered (default "en")

Amounts are decimals, never floats: a float is refused, because 0.1 + 0.2 is not 0.3 in a
float and money must add up.

    from genmo_fb_common.bank_config import to_amount, amount_str, format_amount

    amount = to_amount("50")            # Decimal("50.00") for a 2-decimal currency
    amount_str(amount)                  # "50.00"  (events, JSON)
    format_amount(amount, language="fr")  # "50,00 MRU"  (text people read)

Times are kept in UTC and always carry their time zone; the bank's time zone decides what
"today", "this week" and "this month" mean (payout days, limit periods, streaks).

    from genmo_fb_common.bank_config import utc_now, bank_today, bank_week_start

    utc_now()          # now, in UTC, time zone attached
    bank_today()       # today's date at the bank
    bank_week_start()  # the moment this week began at the bank, in UTC
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

DEFAULT_CURRENCY = "MRU"
DEFAULT_TIMEZONE = "Africa/Nouakchott"
DEFAULT_LANGUAGES = ("en", "fr", "ar")
DEFAULT_LANGUAGE = "en"

# ISO 4217 minor units, where they are not 2
_CURRENCY_DECIMALS = {
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
    "JPY": 0,
    "KRW": 0,
    "XAF": 0,
    "XOF": 0,
}
_RTL_LANGUAGES = {"ar", "fa", "he", "ur"}
# how each language writes a number: (thousands separator, decimal separator)
_NUMBER_SEPARATORS = {"fr": (" ", ",")}
_DEFAULT_SEPARATORS = (",", ".")

Amount = Union[Decimal, int, str]


# --------------------------------------------------------------------------- currency


def bank_currency() -> str:
    """The bank's currency code, e.g. "MRU"."""
    code = getattr(settings, "BANK_CURRENCY", DEFAULT_CURRENCY)
    if not isinstance(code, str) or len(code) != 3 or not code.isalpha():
        raise ImproperlyConfigured(
            f"BANK_CURRENCY must be a 3-letter ISO 4217 code, not {code!r}."
        )
    return code.upper()


def currency_decimals(currency: Optional[str] = None) -> int:
    """How many decimals the currency has (the bank's own when none is given)."""
    own = bank_currency()
    code = (currency or own).upper()
    configured = getattr(settings, "BANK_CURRENCY_DECIMALS", None)
    if configured is not None and code == own:
        if (
            isinstance(configured, bool)
            or not isinstance(configured, int)
            or configured < 0
        ):
            raise ImproperlyConfigured(
                f"BANK_CURRENCY_DECIMALS must be a whole number, not {configured!r}."
            )
        return configured
    return _CURRENCY_DECIMALS.get(code, 2)


def _decimal(value: Amount) -> Decimal:
    # bool is an int in Python, and True is not an amount
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise TypeError(
            f"An amount is a Decimal, an int or a string, never {type(value).__name__}: "
            "floats do not add up."
        )
    try:
        amount = Decimal(value.strip() if isinstance(value, str) else value)
    except InvalidOperation:
        raise ValueError(f"Not an amount: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"Not an amount: {value!r}")
    return amount


def _unit(currency: Optional[str]) -> Decimal:
    return Decimal(1).scaleb(-currency_decimals(currency))


def _quantize(
    amount: Decimal,
    value: Amount,
    currency: Optional[str],
    rounding: str = ROUND_HALF_UP,
) -> Decimal:
    try:
        return amount.quantize(_unit(currency), rounding=rounding)
    except InvalidOperation:
        # too large to hold with the currency's decimals
        raise ValueError(f"Not an amount: {value!r}") from None


def to_amount(value: Amount, currency: Optional[str] = None) -> Decimal:
    """
    The amount as a Decimal with exactly the currency's decimals: "50" becomes
    Decimal("50.00"). A float is a TypeError. More decimals than the currency has is a
    ValueError, so nothing is rounded away unnoticed; `round_amount` rounds on purpose.
    """
    amount = _decimal(value)
    exact = _quantize(amount, value, currency)
    if exact != amount:
        raise ValueError(
            f"{value!r} has more decimals than {(currency or bank_currency()).upper()} "
            f"allows ({currency_decimals(currency)})."
        )
    return exact


def round_amount(
    value: Amount, currency: Optional[str] = None, rounding: str = ROUND_HALF_UP
) -> Decimal:
    """A calculated amount (a share, a percentage) rounded to the currency's decimals."""
    return _quantize(_decimal(value), value, currency, rounding)


def amount_str(value: Amount, currency: Optional[str] = None) -> str:
    """The amount as plain text with the currency's decimals, for events and JSON: "50.00"."""
    return format(to_amount(value, currency), "f")


def format_amount(
    value: Amount, currency: Optional[str] = None, language: Optional[str] = None
) -> str:
    """
    The amount as people read it, with the currency code: "1,234.50 MRU" in English,
    "1 234,50 MRU" in French. Digits stay Western in every language; laying the text out
    right to left is the screen's job (see `is_rtl`).
    """
    code = (currency or bank_currency()).upper()
    amount = to_amount(value, code)
    thousands, decimal_point = _NUMBER_SEPARATORS.get(
        resolve_language(language), _DEFAULT_SEPARATORS
    )
    whole, _, fraction = format(abs(amount), ",f").partition(".")
    text = whole.replace(",", thousands)
    if fraction:
        text += decimal_point + fraction
    sign = "-" if amount < 0 else ""
    return f"{sign}{text} {code}"


# --------------------------------------------------------------------------- languages


def bank_languages() -> tuple:
    """The languages the bank offers, e.g. ("en", "fr", "ar")."""
    return tuple(
        str(code).lower()
        for code in getattr(settings, "BANK_LANGUAGES", DEFAULT_LANGUAGES)
    )


def default_language() -> str:
    return str(getattr(settings, "BANK_DEFAULT_LANGUAGE", DEFAULT_LANGUAGE)).lower()


def resolve_language(language: Optional[str] = None) -> str:
    """
    The offered language for what a caller asked for: "fr-FR" and "FR" give "fr"; a language
    the bank does not offer, or none, gives the default.
    """
    if language:
        code = str(language).replace("_", "-").split("-")[0].strip().lower()
        if code in bank_languages():
            return code
    return default_language()


def is_rtl(language: Optional[str] = None) -> bool:
    """True when the language is written right to left (Arabic)."""
    return resolve_language(language) in _RTL_LANGUAGES


# --------------------------------------------------------------------------- time


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ImproperlyConfigured(
            f"BANK_TIMEZONE must be an IANA time zone name, not {name!r}."
        ) from None


def bank_timezone() -> ZoneInfo:
    """The bank's time zone."""
    return _zone(getattr(settings, "BANK_TIMEZONE", DEFAULT_TIMEZONE))


def utc_now() -> datetime:
    """Now in UTC, with its time zone attached (never a bare datetime)."""
    return datetime.now(timezone.utc)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            "A time without a time zone can't be placed; use utc_now() or attach one."
        )
    return moment


def to_bank_time(moment: datetime) -> datetime:
    """The same moment on the bank's clock."""
    return _aware(moment).astimezone(bank_timezone())


def bank_now() -> datetime:
    """Now on the bank's clock."""
    return to_bank_time(utc_now())


def bank_today(now: Optional[datetime] = None) -> date:
    """Today's date at the bank (which is not always UTC's date)."""
    return to_bank_time(now or utc_now()).date()


def bank_day_start(day: Optional[date] = None) -> datetime:
    """The moment that day began at the bank (today when none is given), in UTC."""
    local = datetime.combine(day or bank_today(), time.min, tzinfo=bank_timezone())
    return local.astimezone(timezone.utc)


def bank_week_start(now: Optional[datetime] = None) -> datetime:
    """The moment the bank's current week began, in UTC (weekly limits and payouts)."""
    first_day = getattr(settings, "BANK_WEEK_START", 0)
    if isinstance(first_day, bool) or first_day not in range(7):
        raise ImproperlyConfigured(
            f"BANK_WEEK_START must be 0 (Monday) to 6 (Sunday), not {first_day!r}."
        )
    today = bank_today(now)
    return bank_day_start(today - timedelta(days=(today.weekday() - first_day) % 7))


def bank_month_start(now: Optional[datetime] = None) -> datetime:
    """The moment the bank's current month began, in UTC (monthly limits and budgets)."""
    return bank_day_start(bank_today(now).replace(day=1))
