"""
Tests for bank configuration (FC-09): currency, amounts, languages and time from settings.

FC-09's check is that the same tests pass with two different bank configurations, so the
tests that depend on the bank run once per bank below.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from genmo_fb_common import bank_config
from genmo_fb_common.bank_config import (
    amount_str,
    bank_currency,
    bank_day_start,
    bank_languages,
    bank_month_start,
    bank_now,
    bank_timezone,
    bank_today,
    bank_week_start,
    currency_decimals,
    format_amount,
    is_rtl,
    resolve_language,
    round_amount,
    to_amount,
    to_bank_time,
    utc_now,
)

# Two banks that differ in everything FC-09 covers: currency, decimals, clock, week, languages
BANKS = {
    "mauritania": {
        "BANK_CURRENCY": "MRU",
        "BANK_TIMEZONE": "Africa/Nouakchott",  # UTC+0
        "BANK_WEEK_START": 0,
        "BANK_LANGUAGES": ("en", "fr", "ar"),
        "BANK_DEFAULT_LANGUAGE": "fr",
    },
    "kuwait": {
        "BANK_CURRENCY": "KWD",  # 3 decimals
        "BANK_TIMEZONE": "Asia/Kuwait",  # UTC+3
        "BANK_WEEK_START": 6,  # Sunday
        "BANK_LANGUAGES": ("ar", "en"),
        "BANK_DEFAULT_LANGUAGE": "ar",
    },
}


@pytest.fixture(params=sorted(BANKS))
def bank(request):
    """Every test using this runs once per bank configuration."""
    config = BANKS[request.param]
    with override_settings(**config):
        yield config


class TestDefaults:
    def test_with_no_settings_the_bank_is_mauritanian(self):
        assert bank_currency() == "MRU"
        assert currency_decimals() == 2
        assert bank_timezone() == ZoneInfo("Africa/Nouakchott")
        assert bank_languages() == ("en", "fr", "ar")
        assert resolve_language(None) == "en"


class TestCurrency:
    def test_currency_comes_from_settings(self, bank):
        assert bank_currency() == bank["BANK_CURRENCY"]

    def test_decimals_follow_the_currency(self, bank):
        assert currency_decimals() == {"MRU": 2, "KWD": 3}[bank["BANK_CURRENCY"]]

    def test_decimals_of_another_currency(self, bank):
        assert currency_decimals("JPY") == 0
        assert currency_decimals("usd") == 2

    def test_decimals_can_be_set(self):
        with override_settings(BANK_CURRENCY="XYZ", BANK_CURRENCY_DECIMALS=4):
            assert currency_decimals() == 4
            assert to_amount("1.2345") == Decimal("1.2345")
            assert currency_decimals("USD") == 2  # only the bank's own

    @pytest.mark.parametrize("code", ["", "MR", "OUGUIYA", "M1U", None, 12])
    def test_a_bad_currency_is_a_configuration_error(self, code):
        with override_settings(BANK_CURRENCY=code), pytest.raises(ImproperlyConfigured):
            bank_currency()

    @pytest.mark.parametrize("decimals", [-1, 2.0, "2", True])
    def test_bad_decimals_are_a_configuration_error(self, decimals):
        with (
            override_settings(BANK_CURRENCY_DECIMALS=decimals),
            pytest.raises(ImproperlyConfigured),
        ):
            currency_decimals()


class TestAmounts:
    def test_an_amount_gets_the_currencys_decimals(self, bank):
        places = currency_decimals()

        amount = to_amount("50")

        assert amount == Decimal(50)
        assert -amount.as_tuple().exponent == places
        assert amount_str("50") == "50." + "0" * places

    @pytest.mark.parametrize("value", [Decimal("12.5"), "12.5", " 12.50 ", 12])
    def test_decimals_strings_and_ints_are_accepted(self, bank, value):
        assert to_amount(value) == Decimal(str(value).strip())

    @pytest.mark.parametrize("value", [12.5, 0.1, float("nan"), True, None, b"1", [1]])
    def test_a_float_is_never_an_amount(self, bank, value):
        with pytest.raises(TypeError):
            to_amount(value)

    @pytest.mark.parametrize(
        "value", ["", "abc", "1,5", "NaN", "Infinity", "1e999999999"]
    )
    def test_what_is_not_a_number_is_refused(self, bank, value):
        with pytest.raises(ValueError):
            to_amount(value)

    def test_too_many_decimals_are_refused_not_rounded(self, bank):
        too_fine = "1." + "1" * (currency_decimals() + 1)

        with pytest.raises(ValueError):
            to_amount(too_fine)

    def test_floats_would_not_add_up_but_amounts_do(self, bank):
        assert 0.1 + 0.2 != 0.3
        assert to_amount("0.1") + to_amount("0.2") == to_amount("0.3")

    def test_rounding_is_on_purpose(self, bank):
        places = currency_decimals()
        half = "0." + "0" * places + "5"  # half of the smallest unit

        assert round_amount(half) == Decimal(1).scaleb(-places)  # half up
        assert round_amount(half, rounding=ROUND_HALF_EVEN) == 0
        assert -round_amount("10").as_tuple().exponent == places

    def test_rounding_refuses_a_float_too(self, bank):
        with pytest.raises(TypeError):
            round_amount(10 / 3)

    def test_another_currency_can_be_named(self, bank):
        assert amount_str("1500", "JPY") == "1500"
        with pytest.raises(ValueError):
            to_amount("1500.5", "JPY")


class TestFormatting:
    def test_the_banks_currency_is_shown(self, bank):
        text = format_amount("1234567.5", language="en")
        places = currency_decimals()

        assert text == f"1,234,567.5{'0' * (places - 1)} {bank['BANK_CURRENCY']}"

    def test_french_writes_numbers_its_own_way(self):
        with override_settings(**BANKS["mauritania"]):
            assert format_amount("1234567.5", language="fr") == ("1 234 567,50 MRU")

    def test_arabic_keeps_western_digits(self):
        with override_settings(**BANKS["mauritania"]):
            assert format_amount("1234.5", language="ar") == "1,234.50 MRU"

    def test_no_language_means_the_banks_default(self):
        with override_settings(**BANKS["mauritania"]):  # default: French
            assert format_amount("1234.5") == "1 234,50 MRU"

    def test_negative_and_zero(self, bank):
        code = bank["BANK_CURRENCY"]
        zeros = "0" * currency_decimals()

        assert format_amount("-5", language="en") == f"-5.{zeros} {code}"
        assert format_amount(0, language="en") == f"0.{zeros} {code}"

    def test_a_currency_without_decimals(self, bank):
        assert format_amount("1500", "JPY", language="en") == "1,500 JPY"

    def test_a_float_is_refused(self, bank):
        with pytest.raises(TypeError):
            format_amount(1234.56)

    def test_no_currency_is_hard_coded(self):
        with override_settings(BANK_CURRENCY="XYZ"):
            text = format_amount("1", language="en")

        assert text.endswith("XYZ")
        for code in ("PKR", "SAR", "MRU"):
            assert code not in text


class TestLanguages:
    def test_the_offered_languages_come_from_settings(self, bank):
        assert bank_languages() == bank["BANK_LANGUAGES"]

    def test_a_region_or_case_does_not_matter(self, bank):
        assert resolve_language("AR") == "ar"
        assert resolve_language("en-GB") == "en"
        assert resolve_language("en_US") == "en"

    @pytest.mark.parametrize("asked", [None, "", "de", "zz-ZZ"])
    def test_a_language_not_offered_gives_the_default(self, bank, asked):
        assert resolve_language(asked) == bank["BANK_DEFAULT_LANGUAGE"]

    def test_french_is_only_offered_where_the_bank_offers_it(self, bank):
        offered = "fr" in bank["BANK_LANGUAGES"]

        assert (resolve_language("fr") == "fr") is offered

    def test_arabic_is_right_to_left(self, bank):
        assert is_rtl("ar") is True
        assert is_rtl("en") is False


class TestTime:
    def test_now_carries_its_time_zone(self):
        now = utc_now()

        assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
        assert abs(now - datetime.now(timezone.utc)) < timedelta(seconds=5)

    def test_the_time_zone_comes_from_settings(self, bank):
        assert bank_timezone() == ZoneInfo(bank["BANK_TIMEZONE"])
        assert bank_now().tzinfo == ZoneInfo(bank["BANK_TIMEZONE"])

    def test_the_same_moment_on_the_banks_clock(self, bank):
        moment = datetime(2026, 11, 13, 22, 30, tzinfo=timezone.utc)

        local = to_bank_time(moment)

        assert local == moment  # the same moment
        assert local.utcoffset() == ZoneInfo(bank["BANK_TIMEZONE"]).utcoffset(moment)

    def test_today_is_the_banks_date_not_utcs(self, bank):
        # Friday 22:30 UTC: still Friday in Nouakchott, already Saturday in Kuwait
        moment = datetime(2026, 11, 13, 22, 30, tzinfo=timezone.utc)
        expected = {
            "Africa/Nouakchott": date(2026, 11, 13),
            "Asia/Kuwait": date(2026, 11, 14),
        }

        assert bank_today(moment) == expected[bank["BANK_TIMEZONE"]]

    def test_a_day_starts_at_the_banks_midnight(self, bank):
        start = bank_day_start(date(2026, 11, 14))
        expected = {
            "Africa/Nouakchott": datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc),
            "Asia/Kuwait": datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc),
        }

        assert start == expected[bank["BANK_TIMEZONE"]]
        assert start.utcoffset() == timedelta(0)  # handed back in UTC

    def test_the_week_starts_on_the_banks_first_day(self, bank):
        # Wednesday 11 November 2026, noon UTC
        moment = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
        expected = {
            # Monday 9 November, midnight in Nouakchott
            "Africa/Nouakchott": datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc),
            # Sunday 8 November, midnight in Kuwait
            "Asia/Kuwait": datetime(2026, 11, 7, 21, 0, tzinfo=timezone.utc),
        }

        assert bank_week_start(moment) == expected[bank["BANK_TIMEZONE"]]

    def test_the_first_day_of_the_week_is_its_own_start(self, bank):
        start = bank_week_start(datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))

        assert bank_week_start(start) == start
        assert bank_week_start(start - timedelta(seconds=1)) == start - timedelta(
            days=7
        )

    def test_the_month_starts_at_the_banks_midnight(self, bank):
        # 30 November 22:30 UTC: November in Nouakchott, already December in Kuwait
        moment = datetime(2026, 11, 30, 22, 30, tzinfo=timezone.utc)
        expected = {
            "Africa/Nouakchott": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc),
            "Asia/Kuwait": datetime(2026, 11, 30, 21, 0, tzinfo=timezone.utc),
        }

        assert bank_month_start(moment) == expected[bank["BANK_TIMEZONE"]]

    def test_a_time_without_a_time_zone_is_refused(self, bank):
        with pytest.raises(ValueError):
            to_bank_time(datetime(2026, 11, 13, 22, 30))
        with pytest.raises(ValueError):
            bank_today(datetime(2026, 11, 13, 22, 30))

    def test_daylight_saving_is_followed(self):
        with override_settings(BANK_TIMEZONE="Europe/Paris"):
            winter = bank_day_start(date(2026, 1, 15))
            summer = bank_day_start(date(2026, 7, 15))

        assert winter == datetime(2026, 1, 14, 23, 0, tzinfo=timezone.utc)
        assert summer == datetime(2026, 7, 14, 22, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("name", ["Mars/Olympus", "", None, 5])
    def test_a_bad_time_zone_is_a_configuration_error(self, name):
        with override_settings(BANK_TIMEZONE=name), pytest.raises(ImproperlyConfigured):
            bank_timezone()

    @pytest.mark.parametrize("day", [7, -1, "0", True])
    def test_a_bad_week_start_is_a_configuration_error(self, day):
        with (
            override_settings(BANK_WEEK_START=day),
            pytest.raises(ImproperlyConfigured),
        ):
            bank_week_start()


class TestNothingHardCoded:
    def test_no_bank_specific_value_outside_the_defaults(self):
        source = open(bank_config.__file__, encoding="utf-8").read()

        for value in ("PKR", "SAR", "Asia/Karachi"):
            assert f'"{value}"' not in source
