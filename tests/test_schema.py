"""Artifact schema: typed validation, dangling-reference rejection, and
the irreversible-requires-idempotency safety rule."""
import pytest
from pydantic import ValidationError

from cua_runner import schema as sc


def _minimal_capability(**overrides):
    defaults = dict(
        metadata=sc.Metadata(
            capability_id="test_cap", name="Test Capability",
            created_from_goal="do the thing", discovery_run_id="run_test",
            target_app=sc.TargetAppProfile(vendor="mockbank", app_name="teller-console", base_url="http://x/"),
        ),
        inputs=[sc.InputParam(name="member_id", type=sc.ParamType.STRING)],
        outputs=[],
        steps=[
            sc.TypeStep(
                step_id="s1",
                target=sc.Locator(primary=sc.LocatorStrategy(strategy=sc.LocatorStrategyKind.ROLE_NAME, role="textbox", name="Member ID")),
                value=sc.InputRef(name="member_id"),
            ),
        ],
        checkpoint=sc.Condition(kind=sc.ConditionKind.URL_MATCH, url_pattern="/done"),
        policy=sc.Policy(
            risk_level=sc.RiskLevel.SAFE,
            allowed_scope=sc.AllowedScope(domains=["x"], routes=["/*"], action_types=["type"]),
        ),
    )
    defaults.update(overrides)
    return sc.Capability(**defaults)


def test_valid_capability_constructs_and_serializes():
    cap = _minimal_capability()
    assert cap.metadata.capability_id == "test_cap"
    payload = cap.model_dump_json()
    assert "member_id" in payload
    # round-trips through JSON validation
    sc.Capability.model_validate_json(payload)


def test_dangling_input_ref_is_rejected():
    with pytest.raises(ValidationError, match="undeclared input"):
        _minimal_capability(steps=[
            sc.TypeStep(
                step_id="s1",
                target=sc.Locator(primary=sc.LocatorStrategy(strategy=sc.LocatorStrategyKind.ROLE_NAME, role="textbox", name="X")),
                value=sc.InputRef(name="does_not_exist"),
            ),
        ])


def test_dangling_output_ref_is_rejected():
    with pytest.raises(ValidationError, match="undeclared output"):
        _minimal_capability(steps=[
            sc.ExtractStep(
                step_id="s1",
                target=sc.Locator(primary=sc.LocatorStrategy(strategy=sc.LocatorStrategyKind.ROLE_NAME, role="cell", name="X")),
                output=sc.OutputRef(name="not_declared"),
            ),
        ])


def test_duplicate_step_ids_rejected():
    step = sc.ClickStep(
        step_id="dup",
        target=sc.Locator(primary=sc.LocatorStrategy(strategy=sc.LocatorStrategyKind.ROLE_NAME, role="button", name="X")),
    )
    with pytest.raises(ValidationError, match="duplicate step_id"):
        _minimal_capability(inputs=[], steps=[step, step])


def test_irreversible_without_idempotency_is_rejected():
    with pytest.raises(ValidationError, match="idempotency"):
        sc.Policy(
            risk_level=sc.RiskLevel.IRREVERSIBLE,
            allowed_scope=sc.AllowedScope(domains=["x"], routes=["/*"], action_types=["click"]),
        )


def test_irreversible_with_idempotency_is_accepted():
    policy = sc.Policy(
        risk_level=sc.RiskLevel.IRREVERSIBLE,
        allowed_scope=sc.AllowedScope(domains=["x"], routes=["/*"], action_types=["click"]),
        idempotency=sc.Idempotency(),
    )
    assert policy.idempotency.on_retry == sc.RetryPolicy.ABORT_AND_ESCALATE


def test_condition_requires_kind_specific_fields():
    with pytest.raises(ValidationError, match="requires field"):
        sc.Condition(kind=sc.ConditionKind.URL_MATCH)  # missing url_pattern


def test_outcome_ordering_is_preserved():
    """outcomes[] evaluation is first-match-wins -- the schema must
    preserve declaration order, not silently reorder or dedupe."""
    cap = _minimal_capability(outcomes=[
        sc.Outcome(name="a", kind=sc.OutcomeKind.BUSINESS_OUTCOME,
                   detect=sc.Condition(kind=sc.ConditionKind.URL_MATCH, url_pattern="/a")),
        sc.Outcome(name="b", kind=sc.OutcomeKind.HARD_FAILURE,
                   detect=sc.Condition(kind=sc.ConditionKind.URL_MATCH, url_pattern="/b")),
    ])
    assert [o.name for o in cap.outcomes] == ["a", "b"]
