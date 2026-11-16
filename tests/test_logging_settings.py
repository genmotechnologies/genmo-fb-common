"""
COM-028 (PL10 / FC-08): `logging_settings` gives a Django service structured, redacted
JSON logs for what it logs with `logging.getLogger`, as well as for structlog.
"""

import json
import logging
import logging.config

import pytest
import structlog

from genmo_fb_common import logs, logscan

CARD = "4111 1111 1111 1111"
PHONE = "+22212345678"


@pytest.fixture
def configure(tmp_path, monkeypatch):
    """Apply logging_settings as Django would; put the logging system back afterwards."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    monkeypatch.delenv("LOG_FILE", raising=False)

    def apply(**kwargs):
        logging.config.dictConfig(logs.logging_settings("test-service", **kwargs))

    yield apply
    for handler in root.handlers:
        handler.close()
    root.handlers[:], root.level = saved
    for name in ("django", "django.server"):
        logging.getLogger(name).handlers = []
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_a_standard_logger_writes_one_redacted_json_line(configure, capsys):
    configure()
    structlog.contextvars.bind_contextvars(request_id="req-1")

    logging.getLogger("apps.cards").info(
        "card %s for %s", CARD, PHONE, extra={"otp": "123456", "card_id": "C-1"}
    )

    [line] = lines(capsys.readouterr().out)
    assert line["event"] == f"card {logs.MASK} for {logs.MASK}"
    assert line["otp"] == logs.MASK
    assert line["card_id"] == "C-1"
    assert line["level"] == "info"
    assert line["logger"] == "apps.cards"
    assert line["service"] == "test-service"
    assert line["request_id"] == "req-1"
    assert line["timestamp"].endswith("Z")


def test_structlog_goes_through_the_same_redaction(configure, capsys):
    configure()

    structlog.get_logger("genmo").info("sent", token="abc.def", phone=PHONE)

    [line] = lines(capsys.readouterr().out)
    assert line["event"] == "sent"
    assert line["token"] == logs.MASK and line["phone"] == logs.MASK
    assert line["service"] == "test-service"


def test_an_exception_text_is_redacted(configure, capsys):
    configure()

    try:
        raise ValueError(f"bad card {CARD}")
    except ValueError:
        logging.getLogger("apps").exception("failed")

    [line] = lines(capsys.readouterr().out)
    assert "ValueError" in line["exception"]
    assert CARD not in line["exception"]


def test_djangos_own_loggers_are_redacted_too(configure, capsys):
    configure()

    logging.getLogger("django.request").error("Bearer abcdefghijkl refused")

    [line] = lines(capsys.readouterr().out)
    assert line["event"] == f"Bearer {logs.MASK} refused"


def test_below_the_level_nothing_is_written(configure, capsys):
    configure(level="warning")

    logging.getLogger("apps").info("quiet")

    assert capsys.readouterr().out == ""


def test_log_file_from_the_environment_passes_the_ci_scan(
    configure, tmp_path, monkeypatch
):
    path = tmp_path / "test.log"
    monkeypatch.setenv("LOG_FILE", str(path))
    configure()

    logging.getLogger("apps").info("login ok", extra={"pin": "1234"})
    logging.getLogger("apps").info("card %s", CARD)
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = path.read_text(encoding="utf-8")
    assert len(lines(text)) == 2
    assert list(logscan.scan(text.splitlines())) == []


def test_without_log_file_only_stdout(configure):
    configure()

    assert [type(h).__name__ for h in logging.getLogger().handlers] == ["StreamHandler"]
