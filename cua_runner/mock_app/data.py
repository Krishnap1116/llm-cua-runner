"""In-memory data + business rules for the mock legacy bank app.

No database, no real customer data -- see REPORT.md for why this is a
deliberately shallow domain simulation around genuinely real mechanics
(session expiry, validation, status gating, locking).
"""
import time

from werkzeug.security import generate_password_hash

SESSION_TTL_SECONDS = 90
LOCK_DURATION_SECONDS = 30
MIN_INITIAL_DEPOSIT = 25.0
MAX_NICKNAME_LENGTH = 100
MAX_SUBACCOUNTS = 3
BUG_DEPOSIT_CENTS = 100_000_000  # exactly $1,000,000.00 trips a real unhandled edge case

SERVER_START = time.time()

DEMO_USERNAME = "teller1"
DEMO_PASSWORD_HASH = generate_password_hash("correcthorsebattery")

MEMBERS = {
    "10234": {"name": "Alicia Nguyen", "status": "active", "savings": 4210.55, "checking": 1022.10, "subaccounts": []},
    "10391": {"name": "Marcus Webb", "status": "active", "savings": 812.00, "checking": 340.25, "subaccounts": ["Emergency Fund"]},
    "10555": {"name": "Dana Ruiz", "status": "restricted", "savings": 2000.00, "checking": 500.00, "subaccounts": []},
    "10672": {"name": "Terrence Ok", "status": "active", "savings": 15300.00, "checking": 2100.00, "subaccounts": ["Travel", "Car", "Home Repair"]},
    "10809": {"name": "Priya Shah", "status": "locked", "savings": 960.40, "checking": 210.00, "subaccounts": []},
    "10920": {"name": "Wendell Cho", "status": "closed", "savings": 0.00, "checking": 0.00, "subaccounts": []},
    "11045": {"name": "Grace Muli", "status": "active", "savings": 5500.00, "checking": 780.00, "subaccounts": []},
    "11187": {"name": "Sam Okafor", "status": "active", "savings": 1150.75, "checking": 600.00, "subaccounts": ["Wedding", "Taxes"]},
}


def effective_status(member_id: str) -> str:
    """A 'locked' member auto-unlocks after LOCK_DURATION_SECONDS, modeling
    another teller session finishing its edit -- a real workflow, not a
    test-convenience toggle."""
    m = MEMBERS[member_id]
    if m["status"] == "locked" and (time.time() - SERVER_START) >= LOCK_DURATION_SECONDS:
        return "active"
    return m["status"]


def is_eligible_for_new_subaccount(member_id: str) -> tuple[bool, str | None]:
    if member_id not in MEMBERS:
        return False, "not_found"
    status = effective_status(member_id)
    if status == "restricted":
        return False, "restricted"
    if status == "closed":
        return False, "closed"
    if status == "locked":
        return False, "locked"
    if len(MEMBERS[member_id]["subaccounts"]) >= MAX_SUBACCOUNTS:
        return False, "cap_reached"
    return True, None
