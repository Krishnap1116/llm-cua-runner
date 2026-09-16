"""Mock app business rules and security mechanics, via Flask's test
client (fast, no browser needed for these -- Playwright is reserved for
the real accessibility-tree-driven tests)."""
import re

from cua_runner.mock_app.app import app as flask_app


def _client():
    return flask_app.test_client()


def _login(client):
    resp = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', resp.text).group(1)
    client.post("/login", data={"csrf_token": token, "username": "teller1", "password": "correcthorsebattery"})


def _csrf(client, path):
    resp = client.get(path)
    return re.search(r'name="csrf_token" value="([^"]+)"', resp.text).group(1)


def test_unauthenticated_request_redirects_to_login():
    resp = _client().get("/member/10234", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_wrong_password_rejected():
    client = _client()
    token = _csrf(client, "/login")
    resp = client.post("/login", data={"csrf_token": token, "username": "teller1", "password": "wrong"})
    assert b"Invalid username or password" in resp.data


def test_search_nonexistent_member_is_a_normal_result_not_a_crash():
    client = _client()
    _login(client)
    resp = client.get("/search?q=99999")
    assert resp.status_code == 200
    assert b"No member found" in resp.data


def test_restricted_member_shows_banner_and_blocks_action():
    client = _client()
    _login(client)
    resp = client.get("/member/10555/new-subaccount")
    assert b"restricted" in resp.data.lower()
    assert b'name="account_type"' not in resp.data  # form not even offered


def test_restricted_member_blocked_server_side_even_with_valid_csrf():
    """Defense in depth: server re-checks eligibility on POST regardless
    of what the UI showed -- never trusts the client."""
    client = _client()
    _login(client)
    token = _csrf(client, "/search")  # any page in the session has a valid token
    resp = client.post("/member/10555/new-subaccount", data={
        "csrf_token": token, "account_type": "sub_savings", "initial_deposit": "500", "nickname": "Should Not Work",
    })
    assert b"restricted" in resp.data.lower()


def test_deposit_below_minimum_is_validation_error():
    client = _client()
    _login(client)
    token = _csrf(client, "/member/10234/new-subaccount")
    resp = client.post("/member/10234/new-subaccount", data={
        "csrf_token": token, "account_type": "sub_savings", "initial_deposit": "10", "nickname": "Too Small",
    })
    assert b"at least" in resp.data


def test_missing_csrf_token_is_rejected():
    client = _client()
    _login(client)
    resp = client.post("/member/10234/new-subaccount", data={
        "csrf_token": "forged", "account_type": "sub_savings", "initial_deposit": "500", "nickname": "X",
    })
    assert resp.status_code == 400


def test_happy_path_creates_subaccount_with_real_account_number():
    client = _client()
    _login(client)
    token = _csrf(client, "/member/10234/new-subaccount")
    client.post("/member/10234/new-subaccount", data={
        "csrf_token": token, "account_type": "sub_savings", "initial_deposit": "500", "nickname": "Vacation Fund",
    })
    token2 = _csrf(client, "/member/10234/new-subaccount/confirm")
    resp = client.post("/member/10234/new-subaccount/confirm", data={"csrf_token": token2})
    assert b"SA-10234-001" in resp.data


def test_cap_reached_after_max_subaccounts():
    client = _client()
    _login(client)
    resp = client.get("/member/10672/new-subaccount")  # Terrence Ok, already at 3
    assert b"maximum number" in resp.data


def test_exact_million_deposit_triggers_real_unhandled_bug_safely():
    """The genuine, undiscovered edge case -- caller gets a safe generic
    error, never a leaked stack trace."""
    client = _client()
    _login(client)
    token = _csrf(client, "/member/10234/new-subaccount")
    resp = client.post("/member/10234/new-subaccount", data={
        "csrf_token": token, "account_type": "sub_savings", "initial_deposit": "1000000", "nickname": "Big",
    })
    assert resp.status_code == 500
    assert b"Traceback" not in resp.data
    assert b"unexpected error" in resp.data.lower()


def test_html_injection_in_search_is_escaped_not_executed():
    client = _client()
    _login(client)
    resp = client.get("/search?q=" + "<script>alert(1)</script>")
    assert b"<script>alert(1)</script>" not in resp.data  # must be escaped, not raw
    assert b"No member found" in resp.data
