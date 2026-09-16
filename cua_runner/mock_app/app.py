"""Mock legacy bank servicing console.

A deliberately hostile-markup (tables, no test IDs), server-rendered Flask
app standing in for a real core-banking screen. See REPORT.md for why every
mechanic here (session TTL, CSRF, status gating, locking, the unhandled
edge case) is real behavior rather than a test-convenience shortcut.
"""
import logging
import os
import time
import traceback

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from cua_runner.mock_app.data import (
    BUG_DEPOSIT_CENTS,
    DEMO_PASSWORD_HASH,
    DEMO_USERNAME,
    MAX_NICKNAME_LENGTH,
    MEMBERS,
    MIN_INITIAL_DEPOSIT,
    SESSION_TTL_SECONDS,
    effective_status,
    is_eligible_for_new_subaccount,
)
from cua_runner.mock_app.security import (
    get_or_create_csrf_token,
    safe_next_path,
    validate_csrf,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000  # 1MB request-body cap

logger = logging.getLogger("mock_app")
logging.basicConfig(level=logging.INFO)

PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def enforce_session():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    authed = session.get("authenticated")
    last_activity = session.get("last_activity", 0)
    if not authed or (time.time() - last_activity) > SESSION_TTL_SECONDS:
        session.clear()
        return redirect(url_for("login", next=request.path))
    session["last_activity"] = time.time()


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


@app.errorhandler(500)
def handle_hard_failure(e):
    # Full detail goes to our own server-side log, never to the caller --
    # this is the "expected/recoverable/hard" separation applied at the
    # app boundary itself, not just in the replay engine.
    logger.error("Unhandled server error: %s\n%s", e, traceback.format_exc())
    return render_template("error.html"), 500


@app.route("/login", methods=["GET", "POST"])
def login():
    csrf_token = get_or_create_csrf_token(session)
    error = None
    if request.method == "POST":
        if not validate_csrf(session, request.form.get("csrf_token")):
            error = "Your session token was invalid. Please try again."
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if username == DEMO_USERNAME and check_password_hash(DEMO_PASSWORD_HASH, password):
                session.clear()
                session["authenticated"] = True
                session["last_activity"] = time.time()
                csrf_token = get_or_create_csrf_token(session)
                return redirect(safe_next_path(request.args.get("next"), default="/search"))
            error = "Invalid username or password."
    return render_template("login.html", csrf_token=csrf_token, error=error)


@app.route("/logout", methods=["POST"])
def logout():
    if not validate_csrf(session, request.form.get("csrf_token")):
        return render_template("error.html"), 400
    session.clear()
    return redirect(url_for("login"))


@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    results = []
    searched = bool(query)
    if searched:
        if query in MEMBERS:
            results = [{"id": query, **MEMBERS[query]}]
        else:
            q_lower = query.lower()
            results = [
                {"id": mid, **m} for mid, m in MEMBERS.items() if q_lower in m["name"].lower()
            ]
    return render_template(
        "search.html",
        query=query,
        searched=searched,
        results=results,
        csrf_token=get_or_create_csrf_token(session),
    )


@app.route("/member/<member_id>", methods=["GET"])
def member_detail(member_id):
    if member_id not in MEMBERS:
        return render_template("member.html", member=None, member_id=member_id)
    member = MEMBERS[member_id]
    status = effective_status(member_id)
    return render_template(
        "member.html",
        member=member,
        member_id=member_id,
        status=status,
        eligible=is_eligible_for_new_subaccount(member_id)[0],
    )


@app.route("/member/<member_id>/new-subaccount", methods=["GET", "POST"])
def new_subaccount(member_id):
    csrf_token = get_or_create_csrf_token(session)
    eligible, reason = is_eligible_for_new_subaccount(member_id)

    if request.method == "GET":
        if member_id not in MEMBERS:
            return render_template("member.html", member=None, member_id=member_id)
        return render_template(
            "new_subaccount.html",
            member=MEMBERS[member_id],
            member_id=member_id,
            eligible=eligible,
            reason=reason,
            csrf_token=csrf_token,
            error=None,
            form_values={},
        )

    # POST: never trust that the form was even shown -- re-check for real.
    if not validate_csrf(session, request.form.get("csrf_token")):
        return render_template("error.html"), 400

    if not eligible:
        return render_template(
            "new_subaccount.html",
            member=MEMBERS.get(member_id),
            member_id=member_id,
            eligible=False,
            reason=reason,
            csrf_token=csrf_token,
            error=None,
            form_values={},
        ), 200

    account_type = request.form.get("account_type", "")
    deposit_raw = request.form.get("initial_deposit", "").strip()
    nickname = request.form.get("nickname", "").strip()
    form_values = {"account_type": account_type, "initial_deposit": deposit_raw, "nickname": nickname}

    error = None
    deposit = None
    if not account_type:
        error = "Please choose an account type."
    elif not nickname:
        error = "Nickname is required."
    elif len(nickname) > MAX_NICKNAME_LENGTH:
        error = f"Nickname must be {MAX_NICKNAME_LENGTH} characters or fewer."
    elif nickname.lower() in [n.lower() for n in MEMBERS[member_id]["subaccounts"]]:
        error = "A sub-account with that nickname already exists."
    else:
        try:
            deposit = float(deposit_raw)
        except ValueError:
            error = "Initial deposit must be a number."
        else:
            if deposit < MIN_INITIAL_DEPOSIT:
                error = f"Initial deposit must be at least ${MIN_INITIAL_DEPOSIT:.2f}."

    if error:
        return render_template(
            "new_subaccount.html",
            member=MEMBERS[member_id],
            member_id=member_id,
            eligible=True,
            reason=None,
            csrf_token=csrf_token,
            error=error,
            form_values=form_values,
        )

    # A real, undiscovered legacy bug: a "days to projected ceiling"
    # calculation nobody guarded against a zero denominator. Reachable only
    # by choosing this exact deposit amount -- not a forced/injected error.
    if round(deposit * 100) == BUG_DEPOSIT_CENTS:
        ceiling = 1_000_000.0
        _days_to_ceiling = 30 / (ceiling - deposit)  # ZeroDivisionError
        return render_template("error.html"), 500

    session["pending_subaccount"] = {
        "member_id": member_id,
        "account_type": account_type,
        "initial_deposit": deposit,
        "nickname": nickname,
    }
    return redirect(url_for("new_subaccount_confirm", member_id=member_id))


@app.route("/member/<member_id>/new-subaccount/confirm", methods=["GET", "POST"])
def new_subaccount_confirm(member_id):
    pending = session.get("pending_subaccount")
    if not pending or pending["member_id"] != member_id:
        return redirect(url_for("new_subaccount", member_id=member_id))

    if request.method == "GET":
        return render_template(
            "confirm.html",
            member=MEMBERS[member_id],
            member_id=member_id,
            pending=pending,
            csrf_token=get_or_create_csrf_token(session),
            account_number=None,
        )

    # POST: commit. Re-validate eligibility defensively -- state may have
    # changed between showing this page and the confirm click.
    if not validate_csrf(session, request.form.get("csrf_token")):
        return render_template("error.html"), 400

    eligible, reason = is_eligible_for_new_subaccount(member_id)
    if not eligible:
        session.pop("pending_subaccount", None)
        return render_template(
            "new_subaccount.html",
            member=MEMBERS.get(member_id),
            member_id=member_id,
            eligible=False,
            reason=reason,
            csrf_token=get_or_create_csrf_token(session),
            error=None,
            form_values={},
        )

    MEMBERS[member_id]["subaccounts"].append(pending["nickname"])
    account_number = f"SA-{member_id}-{len(MEMBERS[member_id]['subaccounts']):03d}"
    session.pop("pending_subaccount", None)

    return render_template(
        "confirm.html",
        member=MEMBERS[member_id],
        member_id=member_id,
        pending=pending,
        csrf_token=get_or_create_csrf_token(session),
        account_number=account_number,
    )


@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("search"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
