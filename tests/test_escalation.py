"""Escalation/handoff: intervention evidence, resume behavior, and the
bounded-retry cap -- using a real Playwright page (headless is fine, the
mechanics don't depend on visibility) and simulated operator input."""
import json
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from cua_runner.escalation.handoff import escalate
from cua_runner.replay.engine import replay
from cua_runner.replay.results import ReplayStatus

ARTIFACT = str(Path(__file__).resolve().parents[1] / "artifacts" / "open_savings_subaccount.json")


def test_escalate_writes_full_intervention_evidence(live_app, tmp_path, monkeypatch):
    import cua_runner.escalation.handoff as handoff_mod

    def fake_evidence_dir(escalation_id):
        d = tmp_path / "escalations" / escalation_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr(handoff_mod, "_evidence_dir", fake_evidence_dir)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(live_app)

        with patch("cua_runner.agent.human_checkpoint.ask_yes_no", return_value=True), \
             patch("cua_runner.agent.human_checkpoint.ask_text", return_value="fixed it manually"):
            request = escalate(page, source="replay", capability_or_goal="test_cap",
                                step_id="s5", reason="locator not found")
        browser.close()

    assert request.resolved is True
    assert request.human_notes == "fixed it manually"
    evidence_files = list((tmp_path / "escalations" / request.escalation_id).iterdir())
    assert any(f.name == "intervention.json" for f in evidence_files)
    assert any(f.name == "at_escalation.png" for f in evidence_files)
    saved = json.loads((tmp_path / "escalations" / request.escalation_id / "intervention.json").read_text())
    assert saved["reason"] == "locator not found"
    assert saved["step_id"] == "s5"


def test_escalate_with_no_tty_reports_unresolved(live_app):
    """An unattended run (no human available) must fail safe -- report
    unresolved, never hang or crash."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(live_app)
        # No patch on ask_yes_no/ask_text -- input() will hit EOF in this
        # non-interactive test process, exercising the real NoTTY path.
        request = escalate(page, source="discovery", capability_or_goal="goal", step_id=None, reason="stuck")
        browser.close()
    assert request.resolved is False


def test_replay_escalation_retries_same_step_then_gives_up(live_app, tmp_path):
    """Regression test for the full handoff mechanic: escalation fires,
    the human's 'yes' is captured, the SAME failed step is retried for
    real (not blindly trusted), and since nothing was actually fixed, it
    fails again -- respecting the 1-attempt cap rather than looping."""
    d = json.load(open(ARTIFACT))
    for s in d["steps"]:
        if s["step_id"] == "step_04":
            s["target"]["primary"]["name"] = "This Button Does Not Exist"
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(json.dumps(d))

    with patch("cua_runner.agent.human_checkpoint.ask_yes_no", return_value=True), \
         patch("cua_runner.agent.human_checkpoint.ask_text", return_value="tried but could not fix"):
        result = replay(str(broken_path), {"member_id": "11045", "initial_deposit": 50, "nickname": "X"},
                         base_url_override=live_app, force_unattended=True, allow_escalation=True)

    assert result.status == ReplayStatus.HARD_FAILURE
    assert "step_04" in result.message
