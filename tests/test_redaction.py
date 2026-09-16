"""Secret and financial-data redaction -- applied to incidental evidence
exposure, never to a capability's deliberate declared outputs."""

from cua_runner.agent.redaction import redact_financial_data, redact_secrets


def test_redact_secrets_masks_known_env_values(monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_PASSWORD", "correcthorsebattery")
    text = "the password is correcthorsebattery, do not share"
    redacted = redact_secrets(text)
    assert "correcthorsebattery" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_ignores_trivially_short_values(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "ab")  # too short to safely redact
    text = "some text containing ab as a substring"
    assert redact_secrets(text) == text


def test_redact_financial_data_masks_dollar_amounts():
    text = "Savings Balance: $4210.55, Checking Balance: $1022.10"
    redacted = redact_financial_data(text)
    assert "$4210.55" not in redacted
    assert "$1022.10" not in redacted
    assert "[REDACTED:AMOUNT]" in redacted


def test_redact_financial_data_masks_account_numbers():
    text = "Account Number: SA-10234-001"
    redacted = redact_financial_data(text)
    assert "SA-10234-001" not in redacted
    assert "[REDACTED:ACCOUNT_NUMBER]" in redacted


def test_redact_financial_data_leaves_unrelated_text_alone():
    text = "No member found matching '12345'"
    assert redact_financial_data(text) == text
