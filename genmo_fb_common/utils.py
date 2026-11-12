"""
Utility functions for GenMo services.
"""

import secrets
import string
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union

from genmo_fb_common.bank_config import bank_today, format_amount, utc_now


def generate_invite_code(length: int = 8) -> str:
    """
    Generate a random invite code.

    Format: XXX-XXXXX (e.g., FAM-A3B7C)

    Args:
        length: Total length of random part (default 8)

    Returns:
        Invite code string
    """
    chars = string.ascii_uppercase + string.digits
    # Remove ambiguous characters
    chars = chars.replace("O", "").replace("0", "").replace("I", "").replace("1", "")

    random_part = "".join(secrets.choice(chars) for _ in range(length))
    return f"FAM-{random_part}"


def generate_qr_code_data(invite_code: str, base_url: str) -> str:
    """
    Generate data for QR code.

    Args:
        invite_code: The invite code
        base_url: App deep link base URL

    Returns:
        URL string for QR code
    """
    return f"{base_url}/invite/{invite_code}"


def mask_bank_account(account_number: str) -> str:
    """
    Mask bank account number for display.

    Example: "1234567890" -> "******7890"
    """
    if len(account_number) <= 4:
        return account_number
    return "*" * (len(account_number) - 4) + account_number[-4:]


def mask_phone_number(phone: str) -> str:
    """
    Mask phone number for display.

    Example: "+966501234567" -> "+966*****4567"
    """
    if len(phone) <= 4:
        return phone
    return phone[:4] + "*" * (len(phone) - 8) + phone[-4:]


def calculate_age(birth_date: Union[date, datetime]) -> int:
    """
    Age in whole years, as of today at the bank (FC-09: the bank's date, not the server's).
    """
    today = bank_today()
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


def is_minor(birth_date: Union[date, datetime], adult_age: int = 18) -> bool:
    """
    Check if person is a minor based on birth date.
    """
    return calculate_age(birth_date) < adult_age


def format_currency(amount, currency: Optional[str] = None) -> str:
    """
    The amount as people read it, in the bank's currency unless another is given (FC-09).
    Same as `bank_config.format_amount`; a float is refused.

    Example: format_currency("1234.56") -> "1,234.56 MRU"
    """
    return format_amount(amount, currency)


def get_expiry_time(hours: int = 24) -> datetime:
    """
    The moment `hours` from now, in UTC with its time zone attached.
    """
    return utc_now() + timedelta(hours=hours)


def is_expired(expiry_time: datetime) -> bool:
    """
    Check if a datetime has passed. A datetime without a time zone is read as UTC, which
    is what `get_expiry_time` used to return.
    """
    if expiry_time.tzinfo is None:
        expiry_time = expiry_time.replace(tzinfo=timezone.utc)
    return utc_now() > expiry_time
