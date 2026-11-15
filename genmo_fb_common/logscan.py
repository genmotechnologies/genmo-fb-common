"""
The CI log scan (FC-08): fails when a log still holds a card number, a JWT, a bearer
credential, an international phone number, or a value under a sensitive key (`pin`,
`otp`, `cvv`, `password`, `token`, ...) that is not masked.

    docker compose logs --no-color | python -m genmo_fb_common.logscan
    python -m genmo_fb_common.logscan service.log other.log

It prints each finding with its line number and what kind it is, never the value, and
exits 1 when there is any, so a pipeline can fail on it. The rules are the ones the
redaction in `genmo_fb_common.logs` applies, so a service that logs through it passes.
"""

import json
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Iterator

from .logs import (
    _BEARER,
    _CARD,
    _JWT,
    _PHONE,
    MASK,
    is_sensitive_key,
    luhn_valid,
)

# `"pin": "1234"`, `pin=1234`, `otp: 123456`: a sensitive key followed by a value
_KEY_VALUE = re.compile(
    r"""["']?([A-Za-z][\w-]{1,40})["']?\s*[:=]\s*["']?(\[REDACTED\]|[^\s,"'}\]]+)"""
)


_BEARER_MASKED = re.compile(r"(?i)(?:bearer|basic)\s+\[REDACTED\]")


@dataclass(frozen=True)
class Finding:
    line: int
    kind: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.kind}"


def scan_line(text: str) -> Iterator[str]:
    """The kinds of sensitive value one line still holds."""
    if _JWT.search(text):
        yield "jwt"
    for match in _BEARER.finditer(text):
        if MASK not in match.group(0):
            yield "bearer credential"
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            yield "card number"
    if _PHONE.search(text):
        yield "phone number"
    # `Authorization: Bearer [REDACTED]` is masked: the scheme word is not the secret
    masked_auth = _BEARER_MASKED.sub(MASK, text)
    for key, value in _KEY_VALUE.findall(masked_auth):
        if is_sensitive_key(key) and value not in (MASK, "null", "None", "none"):
            yield f"unmasked value under '{key}'"


def scan(lines: Iterable[str]) -> list[Finding]:
    findings = []
    for number, line in enumerate(lines, start=1):
        text = line.rstrip("\n")
        # A JSON log line is scanned as the text it is and as its fields, so a value
        # under a sensitive key is found even in a nested object.
        extra = _json_fields(text)
        for kind in dict.fromkeys([*scan_line(text), *extra]):
            findings.append(Finding(number, kind))
    return findings


def _json_fields(text: str) -> list[str]:
    try:
        body = json.loads(text)
    except ValueError:
        return []
    kinds = []

    def walk(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif (
            key is not None and is_sensitive_key(key) and value not in (MASK, None, "")
        ):
            kinds.append(f"unmasked value under '{key}'")

    walk(body)
    return kinds


def main(argv=None) -> int:
    paths = (argv if argv is not None else sys.argv[1:]) or ["-"]
    findings: list[Finding] = []
    for path in paths:
        if path == "-":
            findings += scan(sys.stdin)
        else:
            with open(path, encoding="utf-8", errors="replace") as handle:
                findings += [Finding(f.line, f"{path}: {f.kind}") for f in scan(handle)]
    for finding in findings:
        print(finding)
    print(f"{len(findings)} sensitive value(s) found in the logs")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
