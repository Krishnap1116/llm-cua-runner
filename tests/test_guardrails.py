"""Allowlist enforcement and risky-click detection."""
import pytest

from cua_runner.guardrails.policy import Allowlist, GuardrailViolation, is_risky_click, resolve_url


def test_allowed_navigate_passes():
    allowlist = Allowlist(domains=["127.0.0.1:5000"], routes=["/search", "/member/*"], action_types=["navigate"])
    allowlist.check_navigate("http://127.0.0.1:5000/member/10234")  # should not raise


def test_navigate_outside_allowed_domain_blocked():
    allowlist = Allowlist(domains=["127.0.0.1:5000"], routes=["/*"], action_types=["navigate"])
    with pytest.raises(GuardrailViolation, match="domain"):
        allowlist.check_navigate("http://evil.example/steal")


def test_navigate_outside_allowed_route_blocked():
    allowlist = Allowlist(domains=["127.0.0.1:5000"], routes=["/search"], action_types=["navigate"])
    with pytest.raises(GuardrailViolation, match="route"):
        allowlist.check_navigate("http://127.0.0.1:5000/admin/delete-everything")


def test_disallowed_action_type_blocked():
    allowlist = Allowlist(domains=["x"], routes=["/*"], action_types=["click", "type"])
    with pytest.raises(GuardrailViolation, match="action type"):
        allowlist.check_action_type("extract")


@pytest.mark.parametrize("name", ["Confirm", "Submit Order", "Delete Account", "Approve Transfer", "authorize"])
def test_risky_click_keywords_detected(name):
    assert is_risky_click(name) is True


@pytest.mark.parametrize("name", ["Search", "Cancel", "Back", "Open Sub-Account", "Log Out"])
def test_non_risky_clicks_not_flagged(name):
    assert is_risky_click(name) is False


def test_resolve_url_leaves_absolute_urls_alone():
    assert resolve_url("http://127.0.0.1:5000/member/123", "http://127.0.0.1:5000/") == "http://127.0.0.1:5000/member/123"


def test_resolve_url_resolves_relative_path_against_base():
    """Regression test: a relative path used to parse as an empty domain
    and get incorrectly blocked by the allowlist, even when it was
    actually in scope -- found via a live discovery run where the model
    used the navigate tool with a relative path."""
    assert resolve_url("/member/123", "http://127.0.0.1:5000/") == "http://127.0.0.1:5000/member/123"


def test_relative_path_now_passes_the_allowlist_it_should():
    allowlist = Allowlist(domains=["127.0.0.1:5000"], routes=["/member/*"], action_types=["navigate"])
    full_url = resolve_url("/member/123", "http://127.0.0.1:5000/")
    allowlist.check_navigate(full_url)  # must not raise
