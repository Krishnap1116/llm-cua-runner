"""Last-line-of-defense redaction: strips known secret values out of any
free text before it's written to an artifact or a log file. Section 3.4:
never persist secrets into artifacts OR logs -- this is the backstop in
case one slips into a goal string, a model transcript, or an error
message, on top of never including credentials in agent-facing text in
the first place.
"""
from __future__ import annotations

import os
import re

_SECRET_ENV_VARS = ["SERVICE_ACCOUNT_PASSWORD", "SERVICE_ACCOUNT_USERNAME", "ANTHROPIC_API_KEY", "FLASK_SECRET_KEY"]


def redact_secrets(text: str) -> str:
    if not text:
        return text
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var)
        if value and len(value) >= 4:  # avoid redacting trivially short/empty values
            text = text.replace(value, "[REDACTED]")
    return text


_DOLLAR_AMOUNT = re.compile(r"\$[\d,]+\.\d{2}")
_ACCOUNT_NUMBER = re.compile(r"\bSA-\d+-\d+\b")


def redact_financial_data(text: str) -> str:
    """Applied ONLY when writing evidence/transcripts to disk -- never to
    the live text sent to the model, which genuinely needs to read real
    balances/account numbers to complete the task. This is the
    persistence-boundary redaction Section 3.4 asks for: full fidelity at
    the point of use, minimized footprint in anything stored to disk."""
    if not text:
        return text
    text = _DOLLAR_AMOUNT.sub("[REDACTED:AMOUNT]", text)
    text = _ACCOUNT_NUMBER.sub("[REDACTED:ACCOUNT_NUMBER]", text)
    return text
