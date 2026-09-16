"""Safety guardrails enforced BEFORE every action executes, not after.

Section 3.4: an explicit, configurable allowlist of domains/routes/action
types. The agent (discovery or replay) must not act outside it -- this is
a hard gate in the execution loop, not a logging afterthought.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from urllib.parse import urlparse


def resolve_url(url: str, base_url: str) -> str:
    """Resolves a possibly-relative URL (e.g. "/member/123") against
    base_url before it's checked against the allowlist -- a raw relative
    path parses as an empty domain and would otherwise always be
    incorrectly blocked, regardless of whether it's actually in scope."""
    if "://" in url:
        return url
    return base_url.rstrip("/") + "/" + url.lstrip("/")


class GuardrailViolation(Exception):
    """Raised when an action would step outside the allowlist. This must
    always stop execution and trigger escalation -- never be silently
    swallowed or retried."""


@dataclass
class Allowlist:
    domains: list[str] = field(default_factory=list)      # e.g. ["127.0.0.1:5000"]
    routes: list[str] = field(default_factory=list)        # glob patterns, e.g. ["/search*", "/member/*"]
    action_types: list[str] = field(default_factory=list)  # e.g. ["navigate","click","type","select","extract"]

    def check_navigate(self, url: str) -> None:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        netloc = parsed.netloc or parsed.path.split("/")[0]
        if self.domains and netloc not in self.domains:
            raise GuardrailViolation(f"domain '{netloc}' is not in the allowlist {self.domains}")
        path = parsed.path or "/"
        if self.routes and not any(fnmatch.fnmatch(path, pat) for pat in self.routes):
            raise GuardrailViolation(f"route '{path}' is not in the allowlist {self.routes}")

    def check_action_type(self, action: str) -> None:
        if self.action_types and action not in self.action_types:
            raise GuardrailViolation(f"action type '{action}' is not in the allowlist {self.action_types}")


# Keywords suggesting a click is an irreversible/committing action -- these
# clicks get an explicit, uncached human confirmation during discovery,
# every time (deliberately NOT cached like field parameterization decisions:
# this is a live safety gate, not a data-classification choice, and caching
# "always allow this button" would itself become the vulnerability).
RISKY_CLICK_KEYWORDS = [
    "confirm", "submit", "approve", "delete", "remove", "close", "transfer",
    "pay", "withdraw", "execute", "commit", "authorize",
]


def is_risky_click(accessible_name: str) -> bool:
    name = (accessible_name or "").lower()
    return any(kw in name for kw in RISKY_CLICK_KEYWORDS)


DEFAULT_ALLOWLIST = Allowlist(
    domains=["127.0.0.1:5000", "localhost:5000"],
    routes=["/login", "/logout", "/search", "/member/*"],
    action_types=["navigate", "click", "type", "select", "extract", "assert"],
)
