"""Deterministic replay against a REAL live app (Flask + Playwright, no
LLM) -- the core end-to-end regression suite. Reuses the actual checked-in
artifact produced by the real discovery run."""
from pathlib import Path

from cua_runner.replay.engine import replay
from cua_runner.replay.results import ReplayStatus

ARTIFACT = str(Path(__file__).resolve().parents[1] / "artifacts" / "open_savings_subaccount.json")


def test_happy_path_succeeds_with_new_parameters(live_app):
    """Regression test for the data-driven-locator bug: must succeed for
    a DIFFERENT member than the one used during discovery."""
    result = replay(ARTIFACT, {"member_id": "11045", "initial_deposit": 250, "nickname": "Test Run"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs["sub_account_number"] == "SA-11045-001"


def test_member_not_found_is_business_outcome_not_crash(live_app):
    result = replay(ARTIFACT, {"member_id": "99999", "initial_deposit": 250, "nickname": "X"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_result == "not_found"


def test_validation_error_below_minimum_deposit(live_app):
    result = replay(ARTIFACT, {"member_id": "11187", "initial_deposit": 10, "nickname": "Too Small"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_result == "validation_error"


def test_restricted_member_is_business_outcome(live_app):
    result = replay(ARTIFACT, {"member_id": "10555", "initial_deposit": 250, "nickname": "X"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_result == "restricted"


def test_cap_reached_is_business_outcome(live_app):
    result = replay(ARTIFACT, {"member_id": "10672", "initial_deposit": 50, "nickname": "One Too Many"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_result == "cap_reached"


def test_real_unhandled_bug_is_hard_failure_not_a_crash(live_app):
    result = replay(ARTIFACT, {"member_id": "11045", "initial_deposit": 1000000, "nickname": "Big"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.HARD_FAILURE
    assert result.debug is not None


def test_draft_capability_blocked_by_default(live_app, tmp_path):
    import json
    d = json.load(open(ARTIFACT))
    d["policy"]["approval_state"] = "draft"
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(d))

    result = replay(str(draft_path), {"member_id": "11045", "initial_deposit": 50, "nickname": "X"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.HARD_FAILURE
    assert "DRAFT" in result.message


def test_draft_capability_proceeds_with_force_unattended(live_app, tmp_path):
    import json
    d = json.load(open(ARTIFACT))
    d["policy"]["approval_state"] = "draft"
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(d))

    result = replay(str(draft_path), {"member_id": "11187", "initial_deposit": 50, "nickname": "X"},
                     base_url_override=live_app, force_unattended=True)
    assert result.status == ReplayStatus.SUCCESS


def test_tampered_allowed_scope_blocks_out_of_scope_route(live_app, tmp_path):
    import json
    d = json.load(open(ARTIFACT))
    d["policy"]["allowed_scope"]["routes"] = ["/login", "/logout", "/search"]  # /member/* removed
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(d))

    result = replay(str(tampered_path), {"member_id": "11045", "initial_deposit": 50, "nickname": "X"},
                     base_url_override=live_app, force_unattended=True)
    assert result.status == ReplayStatus.HARD_FAILURE
    assert "Guardrail" in result.message
    assert result.retry_guidance == "abort_and_escalate"


def test_missing_required_param_rejected_before_touching_browser(live_app):
    result = replay(ARTIFACT, {"initial_deposit": 50, "nickname": "X"}, base_url_override=live_app)
    assert result.status == ReplayStatus.HARD_FAILURE
    assert "member_id" in result.message


def test_wrong_param_type_rejected(live_app):
    result = replay(ARTIFACT, {"member_id": "11045", "initial_deposit": "not a number", "nickname": "X"},
                     base_url_override=live_app)
    assert result.status == ReplayStatus.HARD_FAILURE
    assert "number" in result.message


def test_evidence_is_written_for_every_replay(live_app, tmp_path, monkeypatch):
    import cua_runner.replay.engine as engine_mod
    evidence_root = tmp_path / "evidence" / "replays"

    def fake_evidence_dir(capability_id):
        d = evidence_root / f"replay_test_{capability_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(engine_mod, "_evidence_dir", fake_evidence_dir)
    replay(ARTIFACT, {"member_id": "11045", "initial_deposit": 50, "nickname": "Evidence Test"},
           base_url_override=live_app)
    files = list(evidence_root.rglob("*"))
    assert any(f.name == "result.json" for f in files)
    assert any(f.name == "final_state.png" for f in files)
