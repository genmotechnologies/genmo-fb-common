"""
Structured logging with redaction (FC-08).

No log line may carry a card number, a PIN, an OTP, a token or a phone number. Redaction is
a structlog processor, so it applies to everything a service logs, whatever the call site
did, and it works two ways:

* by key: a value under `pin`, `otp`, `card_number`, `phone`, `authorization`, `token`,
  `password`, `secret`, `cvv` and similar names is replaced whatever it holds;
* by content: a card number (13 to 19 digits that pass the Luhn check), a JWT, a
  `Bearer ...` credential or an international phone number (`+` and 8 to 15 digits) inside
  any string, a message or an error text included, is replaced.

A service opts in once, at start-up:

    from genmo_fb_common.logs import configure_logging
    configure_logging("account-service")

or adds `redact_sensitive` to its own structlog processor list, before the renderer.
`genmo_fb_common.logscan` is the matching check for CI: it fails on a log that still holds
any of these.
"""

import logging
import re
import sys
from typing import Any

import structlog

MASK = "[REDACTED]"

# Words that make a key sensitive. A key is split into words (`card_number`, `cardNumber`,
# `X-Auth-Token` -> card, number / auth, token), so `pin` matches `user_pin` but not
# `shipping`.
SENSITIVE_WORDS = frozenset(
    {
        "pin",
        "otp",
        "cvv",
        "cvc",
        "cvv2",
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "authorization",
        "credential",
        "credentials",
        "pan",
        "msisdn",
        "phone",
        "mobile",
    }
)
# Two-word names that are sensitive although neither word alone is (`card number`).
SENSITIVE_PAIRS = frozenset(
    {("card", "number"), ("card", "no"), ("account", "pin"), ("api", "key")}
)

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_CARD = re.compile(r"(?<![\w.-])(?:\d[ -]?){12,18}\d(?![\w-])")
# International only: `+` then 8 to 15 digits, with the usual separators. A number
# without the `+` cannot be told from an id or a date, so it is caught by its key.
_PHONE = re.compile(r"(?<![\w+])\+\d[\d\s().-]{6,17}\d(?!\d)")


def is_sensitive_key(key: Any) -> bool:
    words = [word.lower() for word in _WORDS.findall(str(key))]
    if any(word in SENSITIVE_WORDS for word in words):
        return True
    return any(pair in SENSITIVE_PAIRS for pair in zip(words, words[1:]))


def luhn_valid(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        number = int(char)
        if position % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _mask_card(match: "re.Match") -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19 and luhn_valid(digits):
        return MASK
    return match.group(0)


def scrub(text: str) -> str:
    """The text with every card number, JWT, bearer credential and phone number masked."""
    text = _JWT.sub(MASK, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _CARD.sub(_mask_card, text)
    return _PHONE.sub(MASK, text)


def redact(value: Any, key: Any = None) -> Any:
    """`value` with what is sensitive masked, through dicts, lists and tuples."""
    if key is not None and is_sensitive_key(key) and value is not None:
        return MASK
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    if isinstance(value, str):
        return scrub(value)
    return value


def redact_sensitive(logger, method_name, event_dict):
    """The structlog processor. Put it before the renderer."""
    return {key: redact(value, key) for key, value in event_dict.items()}


def configure_logging(service: str, level: str = "INFO", json: bool = True) -> None:
    """
    Structured logs for one service: ISO UTC time, level, the service name, the request ID
    RequestIDMiddleware bound for this request, redaction, and JSON (or a console format for
    development with `json=False`). Call once at start-up.
    """
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.CallsiteParameterAdder(
            {structlog.processors.CallsiteParameter.FUNC_NAME}
        ),
        _bind_service(service),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive,  # last before the renderer: nothing added after it escapes
        (
            structlog.processors.JSONRenderer()
            if json
            else structlog.dev.ConsoleRenderer(colors=False)
        ),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def _bind_service(service: str):
    def processor(logger, method_name, event_dict):
        event_dict.setdefault("service", service)
        return event_dict

    return processor
