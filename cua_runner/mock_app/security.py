"""Minimal, real security mechanics: CSRF tokens and open-redirect guard."""
import secrets
from urllib.parse import urlparse

CSRF_SESSION_KEY = "_csrf_token"


def get_or_create_csrf_token(session) -> str:
    if CSRF_SESSION_KEY not in session:
        session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_SESSION_KEY]


def validate_csrf(session, submitted_token: str | None) -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not submitted_token:
        return False
    return secrets.compare_digest(expected, submitted_token)


def safe_next_path(next_value: str | None, default: str = "/search") -> str:
    """Only allow same-origin relative paths -- blocks the classic
    open-redirect via ?next=https://evil.example or ?next=//evil.example."""
    if not next_value:
        return default
    parsed = urlparse(next_value)
    if parsed.scheme or parsed.netloc:
        return default
    if not next_value.startswith("/") or next_value.startswith("//"):
        return default
    return next_value
