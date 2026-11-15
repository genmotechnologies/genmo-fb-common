"""
FC-08: redaction of sensitive values in logs, and the CI scan that checks it.
"""

import json

import pytest
import structlog

from genmo_fb_common import logs, logscan

CARD = "4111 1111 1111 1111"  # a published test card number: Luhn-valid
CARD_PLAIN = "4111111111111111"
NOT_A_CARD = "4111111111111112"  # same shape, fails the Luhn check
PHONE = "+22212345678"
JWT = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6ImsxIn0."
    "eyJzdWIiOiJDVVNULVAxIiwidHlwIjoiYWNjZXNzIn0."
    "c2lnbmF0dXJlLXNpZ25hdHVyZS1zaWduYXR1cmU"
)


class TestKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "pin",
            "PIN",
            "user_pin",
            "otp",
            "otp_code",
            "cvv",
            "password",
            "client_secret",
            "access_token",
            "refreshToken",
            "X-Auth-Token",
            "authorization",
            "card_number",
            "cardNumber",
            "phone",
            "phone_number",
            "mobile",
            "msisdn",
            "api_key",
        ],
    )
    def test_a_sensitive_key_is_masked_whatever_it_holds(self, key):
        assert logs.redact({key: "anything-at-all"}) == {key: logs.MASK}

    @pytest.mark.parametrize(
        "key",
        [
            "event",
            "request_id",
            "session_id",
            "bank_customer_id",
            "shipping",
            "typing",
            "spinning",
            "amount",
            "status",
            "attempt",
        ],
    )
    def test_other_keys_are_left_alone(self, key):
        assert logs.redact({key: "visible"}) == {key: "visible"}

    def test_a_none_stays_none_so_a_missing_value_is_not_hidden(self):
        assert logs.redact({"pin": None}) == {"pin": None}

    def test_nested_values_and_lists_are_reached(self):
        event = {"user": {"profile": {"otp": "123456"}}, "items": [{"token": "abc"}]}
        assert logs.redact(event) == {
            "user": {"profile": {"otp": logs.MASK}},
            "items": [{"token": logs.MASK}],
        }


class TestContent:
    def test_a_card_number_in_a_message_is_masked(self):
        for card in (CARD, CARD_PLAIN, "4111-1111-1111-1111"):
            text = logs.scrub(f"payment for card {card} declined")
            assert "4111" not in text and logs.MASK in text

    def test_a_number_that_fails_the_luhn_check_is_not_a_card(self):
        """Ids and counters have the same shape; masking them would make logs useless."""
        assert logs.scrub(f"transfer number {NOT_A_CARD}") == (
            f"transfer number {NOT_A_CARD}"
        )

    def test_a_jwt_is_masked(self):
        assert JWT.split(".")[0] not in logs.scrub(f"token was {JWT} and failed")

    def test_a_bearer_credential_is_masked_and_the_word_stays(self):
        assert logs.scrub("sent Authorization: Bearer abc123def456ghi") == (
            f"sent Authorization: Bearer {logs.MASK}"
        )

    def test_an_international_phone_number_is_masked(self):
        for phone in (PHONE, "+222 1234 5678", "+92 (300) 123-4567"):
            assert logs.scrub(f"sms to {phone} failed") == f"sms to {logs.MASK} failed"

    def test_dates_timestamps_uuids_and_amounts_are_untouched(self):
        text = (
            "2026-11-15T10:20:30.123456Z transfer 7c9e6679-7425-40de-944b-e07fc1f90ae7 "
            "amount 1234.56 MRU at +00:00 id 20261115102030"
        )
        assert logs.scrub(text) == text

    def test_the_processor_masks_the_event_text_and_fields_together(self):
        result = logs.redact_sensitive(
            None,
            "info",
            {
                "event": f"declined {CARD_PLAIN}",
                "pin": "4321",
                "error": f"no sms to {PHONE}",
                "status": 422,
            },
        )
        assert result == {
            "event": f"declined {logs.MASK}",
            "pin": logs.MASK,
            "error": f"no sms to {logs.MASK}",
            "status": 422,
        }


class TestConfiguredLogging:
    @pytest.fixture(autouse=True)
    def reset(self):
        yield
        structlog.reset_defaults()

    def lines(self, capsys):
        out = capsys.readouterr().out
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def test_a_configured_service_logs_json_with_its_name_and_request_id(self, capsys):
        logs.configure_logging("account-service")
        structlog.contextvars.bind_contextvars(request_id="req-1")
        try:
            structlog.get_logger("x").info("wallet_read", wallet_id="w-1")
        finally:
            structlog.contextvars.clear_contextvars()

        (line,) = self.lines(capsys)
        assert line["event"] == "wallet_read"
        assert line["service"] == "account-service"
        assert line["request_id"] == "req-1"
        assert line["level"] == "info" and line["timestamp"].endswith("Z")

    def test_nothing_sensitive_reaches_the_output_through_any_call_site(self, capsys):
        logs.configure_logging("card-service")
        log = structlog.get_logger("x")

        log.info(f"card {CARD_PLAIN} used", pin="1234", otp="987654")
        log.warning("sms_failed", phone=PHONE, error=f"to {PHONE}", token=JWT)
        log.error("auth_failed", headers={"Authorization": f"Bearer {JWT}"})
        try:
            raise ValueError(f"bad card {CARD_PLAIN}")
        except ValueError:
            log.exception("payment_failed")

        output = json.dumps(self.lines(capsys))
        for secret in (CARD_PLAIN, "1234", "987654", PHONE, JWT, JWT.split(".")[1]):
            assert secret not in output, secret

    def test_the_output_passes_the_ci_log_scan(self, capsys):
        logs.configure_logging("card-service")
        log = structlog.get_logger("x")
        log.info(f"card {CARD_PLAIN}", pin="1234", phone=PHONE, token=JWT)

        assert logscan.scan(capsys.readouterr().out.splitlines()) == []


class TestLogScan:
    def kinds(self, text):
        return [f.kind for f in logscan.scan(text.splitlines())]

    def test_it_finds_each_kind_of_leak(self):
        assert "card number" in self.kinds(f"paid with {CARD}")
        assert "jwt" in self.kinds(f"got {JWT}")
        assert "bearer credential" in self.kinds("Authorization: Bearer abcdefgh12345")
        assert "phone number" in self.kinds(f"sms to {PHONE}")
        assert self.kinds("pin=1234") == ["unmasked value under 'pin'"]
        # found once, whether the line is read as text or as JSON
        assert self.kinds('{"otp": "123456"}') == ["unmasked value under 'otp'"]

    def test_a_value_under_a_sensitive_key_in_a_nested_json_log_is_found(self):
        line = json.dumps({"event": "x", "user": {"profile": {"password": "hunter2"}}})
        assert "unmasked value under 'password'" in self.kinds(line)

    def test_masked_values_pass(self):
        line = json.dumps({"event": "x", "pin": logs.MASK, "token": logs.MASK})
        assert self.kinds(line) == []
        assert self.kinds(f"Authorization: Bearer {logs.MASK}") == []
        assert self.kinds("pin=null") == []

    def test_an_ordinary_log_passes(self):
        line = json.dumps(
            {
                "event": "transfer_completed",
                "transfer_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "amount": "50.00",
                "timestamp": "2026-11-15T10:20:30Z",
                "request_id": "req-1",
            }
        )
        assert self.kinds(line) == []

    def test_a_number_that_fails_the_luhn_check_passes(self):
        assert self.kinds(f"transfer number {NOT_A_CARD}") == []

    def test_findings_name_the_line_and_never_the_value(self, tmp_path, capsys):
        log = tmp_path / "service.log"
        log.write_text(f"fine\npaid with {CARD}\nfine again\n", encoding="utf-8")

        code = logscan.main([str(log)])

        out = capsys.readouterr().out
        assert code == 1
        assert "line 2" in out and "card number" in out
        assert "4111" not in out

    def test_a_clean_log_exits_zero(self, tmp_path, capsys):
        log = tmp_path / "service.log"
        log.write_text('{"event": "ok"}\n', encoding="utf-8")

        assert logscan.main([str(log)]) == 0
        assert "0 sensitive" in capsys.readouterr().out
