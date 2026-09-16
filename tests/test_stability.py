"""Multi-run stability: the key correctness property is distinguishing
genuine flakiness (hard failures) from expected business-state variance
(e.g. legitimately hitting a cap after enough successful runs)."""
from pathlib import Path

from cua_runner.replay.stability import run_stability_test

ARTIFACT = str(Path(__file__).resolve().parents[1] / "artifacts" / "open_savings_subaccount.json")


def test_stability_run_classifies_cap_reached_as_not_flaky(live_app):
    # Marcus Webb starts with 1 existing sub-account (cap 3) -- 2 more
    # succeed, then legitimately hits the cap. That's correct business
    # behavior, not automation flakiness.
    pool = [{"member_id": "10391", "initial_deposit": 50, "nickname": "Stability"}]
    report = run_stability_test(ARTIFACT, pool, n=4, force_unattended=True)

    assert report.success_count == 2
    assert report.business_outcome_counts.get("cap_reached") == 2
    assert report.hard_failure_count == 0
    assert report.flaky is False


def test_stability_run_flags_genuine_hard_failures_as_flaky(live_app, tmp_path):
    import json
    d = json.load(open(ARTIFACT))
    for s in d["steps"]:
        if s["step_id"] == "step_04":
            s["target"]["primary"]["name"] = "This Button Does Not Exist"
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(json.dumps(d))

    pool = [{"member_id": "11045", "initial_deposit": 50, "nickname": "X"}]
    report = run_stability_test(str(broken_path), pool, n=2, force_unattended=True)

    assert report.hard_failure_count == 2
    assert report.flaky is True
