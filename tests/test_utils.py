"""
Tests for the utility functions that depend on the bank's configuration (FC-09).
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.test import override_settings

from genmo_fb_common import utils
from genmo_fb_common.utils import (
    calculate_age,
    format_currency,
    get_expiry_time,
    is_expired,
    is_minor,
)


class TestFormatCurrency:
    def test_uses_the_banks_currency(self):
        with override_settings(BANK_CURRENCY="MRU", BANK_DEFAULT_LANGUAGE="en"):
            assert format_currency("1234.56") == "1,234.56 MRU"

    def test_follows_another_banks_configuration(self):
        with override_settings(BANK_CURRENCY="KWD", BANK_DEFAULT_LANGUAGE="en"):
            assert format_currency("1234.5") == "1,234.500 KWD"

    def test_a_named_currency_wins(self):
        with override_settings(BANK_DEFAULT_LANGUAGE="en"):
            assert format_currency("10", "USD") == "10.00 USD"

    def test_a_float_is_refused(self):
        with pytest.raises(TypeError):
            format_currency(1234.56)


class TestExpiry:
    def test_expiry_time_is_in_utc_with_its_time_zone(self):
        expiry = get_expiry_time(hours=2)

        assert expiry.utcoffset() == timedelta(0)
        assert timedelta(hours=1, minutes=59) < expiry - datetime.now(timezone.utc)
        assert expiry - datetime.now(timezone.utc) <= timedelta(hours=2)

    def test_a_past_time_is_expired_and_a_future_one_is_not(self):
        now = datetime.now(timezone.utc)

        assert is_expired(now - timedelta(seconds=1)) is True
        assert is_expired(now + timedelta(hours=1)) is False
        assert is_expired(get_expiry_time(1)) is False

    def test_a_time_without_a_time_zone_is_read_as_utc(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        assert is_expired(now - timedelta(minutes=1)) is True
        assert is_expired(now + timedelta(minutes=1)) is False

    def test_a_time_in_another_zone_is_compared_as_the_same_moment(self):
        karachi = timezone(timedelta(hours=5))
        in_an_hour = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(
            karachi
        )

        assert is_expired(in_an_hour) is False


class TestAge:
    def test_age_is_counted_on_the_banks_date(self):
        # 31 December 22:30 UTC: the birthday (1 January) has come in Kuwait, not in Nouakchott
        moment = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)
        born = date(2010, 1, 1)

        with patch("genmo_fb_common.bank_config.utc_now", return_value=moment):
            with override_settings(BANK_TIMEZONE="Africa/Nouakchott"):
                assert calculate_age(born) == 16
            with override_settings(BANK_TIMEZONE="Asia/Kuwait"):
                assert calculate_age(born) == 17

    def test_a_datetime_birth_date_still_works(self):
        moment = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

        with patch("genmo_fb_common.bank_config.utc_now", return_value=moment):
            assert calculate_age(datetime(2010, 6, 15)) == 16
            assert calculate_age(datetime(2010, 6, 16)) == 15

    def test_minor(self):
        moment = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

        with patch("genmo_fb_common.bank_config.utc_now", return_value=moment):
            assert is_minor(date(2010, 1, 1)) is True
            assert is_minor(date(2008, 6, 15)) is False
            assert is_minor(date(2008, 6, 15), adult_age=21) is True


class TestNothingHardCoded:
    def test_no_bank_specific_value_and_no_bare_utc_clock(self):
        source = open(utils.__file__, encoding="utf-8").read()

        for value in ("PKR", "SAR", "Asia/Karachi", "utcnow(", "datetime.today("):
            assert value not in source
