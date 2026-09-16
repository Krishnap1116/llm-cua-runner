"""Recorder: turns a DiscoveryResult into a Capability artifact.
Covers the two real bugs we found by hand (credential leakage into the
artifact, naive type-inference) so they can never silently regress."""
from unittest.mock import patch

from cua_runner import schema as sc
from cua_runner.agent.loop import DiscoveryResult, StepRecord
from cua_runner.agent.recorder import build_capability
from cua_runner.guardrails.policy import DEFAULT_ALLOWLIST

TARGET = sc.TargetAppProfile(vendor="mockbank", app_name="teller-console", base_url="http://127.0.0.1:5000")


def _build(steps, outputs=None):
    result = DiscoveryResult(
        run_id="run_test", goal="test goal", status="done", final_message="done",
        steps=steps, outputs=outputs or {}, evidence_dir="evidence/runs/run_test",
    )
    with patch("cua_runner.agent.human_checkpoint.ask_yes_no", return_value=True):
        return build_capability(
            result, capability_id="test_cap", name="Test Capability",
            target_app=TARGET, allowlist=DEFAULT_ALLOWLIST, outcomes=[],
        )


def test_credential_steps_never_appear_in_artifact():
    """Regression test for the real secret-leak bug: login must never be
    persisted, regardless of what field-decision it was given."""
    steps = [
        StepRecord("s1", "type", {"role": "textbox", "name": "Username", "value": "teller1"}, True,
                   param_decision={"credential": True}, excluded_from_artifact=True),
        StepRecord("s2", "type", {"role": "textbox", "name": "Password", "value": "correcthorsebattery"}, True,
                   param_decision={"credential": True}, excluded_from_artifact=True),
        StepRecord("s3", "click", {"role": "button", "name": "Log In"}, True, excluded_from_artifact=True),
        StepRecord("s4", "click", {"role": "link", "name": "Continue"}, True, page_url_after="http://x/done"),
    ]
    cap = _build(steps)
    raw = cap.model_dump_json()
    assert "correcthorsebattery" not in raw
    assert "teller1" not in raw
    assert len(cap.steps) == 1  # only the post-login click survives


def test_explicit_param_type_used_not_guessed():
    """Regression test for the type-inference bug: a numeric-LOOKING
    identifier stays a string when the human said so, rather than being
    silently coerced by a float-parse heuristic."""
    steps = [
        StepRecord("s1", "type", {"role": "textbox", "name": "Member ID", "value": "10234"}, True,
                   param_decision={"parameterize": True, "param_name": "member_id", "param_type": "string"}),
    ]
    cap = _build(steps)
    member_id_input = next(i for i in cap.inputs if i.name == "member_id")
    assert member_id_input.type == sc.ParamType.STRING


def test_extract_without_output_confirmation_is_omitted():
    """An 'extract' the operator declined to expose as an output must not
    appear in the artifact at all -- it was just an internal check."""
    steps = [
        StepRecord("s1", "extract", {"role": "cell", "name": "Internal Check", "output_name": "x"}, True,
                   param_decision={"is_output": False, "output_name": None}),
        StepRecord("s2", "click", {"role": "button", "name": "Next"}, True),
    ]
    cap = _build(steps)
    assert len(cap.outputs) == 0
    assert not any(isinstance(s, sc.ExtractStep) for s in cap.steps)


def test_failed_steps_are_never_recorded():
    steps = [
        StepRecord("s1", "click", {"role": "button", "name": "Broken"}, False, error="not found"),
        StepRecord("s2", "click", {"role": "button", "name": "Works"}, True),
    ]
    cap = _build(steps)
    assert len(cap.steps) == 1
    assert cap.steps[0].step_id == "s2"


def test_risk_level_asked_and_applied():
    steps = [StepRecord("s1", "click", {"role": "button", "name": "Go"}, True, page_url_after="http://x/done")]
    with patch("cua_runner.agent.human_checkpoint.ask_yes_no", side_effect=[True, True]):
        result = DiscoveryResult(run_id="r", goal="g", status="done", final_message="d",
                                  steps=steps, outputs={}, evidence_dir="e")
        cap = build_capability(result, capability_id="c", name="n", target_app=TARGET,
                                allowlist=DEFAULT_ALLOWLIST, outcomes=[])
    assert cap.policy.risk_level == sc.RiskLevel.IRREVERSIBLE
    assert cap.policy.idempotency is not None
