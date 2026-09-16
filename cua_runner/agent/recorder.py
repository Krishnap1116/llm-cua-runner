"""Turns a DiscoveryResult into a typed, validated Capability artifact.

Deterministic transcription, not reinterpretation: every successful step
maps directly onto a schema Step using the SAME role/name the agent used
live, and every value's parameterization was already decided during the
run (via field_memory), not guessed here. Ends with a human review step
before anything is saved -- the safety net against a wrong per-field
answer slipping through unnoticed.
"""
from __future__ import annotations

from pathlib import Path

from cua_runner import schema as sc
from cua_runner.agent import human_checkpoint as hc
from cua_runner.agent.field_memory import looks_sensitive
from cua_runner.agent.redaction import redact_secrets
from cua_runner.agent.loop import DiscoveryResult
from cua_runner.guardrails.policy import Allowlist

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"


def _coerce_literal(value: str) -> sc.Literal_:
    try:
        return sc.Literal_(value=float(value))
    except ValueError:
        return sc.Literal_(value=value)


def _param_type(value: str) -> sc.ParamType:
    try:
        float(value)
        return sc.ParamType.NUMBER
    except ValueError:
        return sc.ParamType.STRING


def _normalize_url(url: str, base_url: str, input_values: set[str]) -> str:
    path = url
    if path.startswith(base_url):
        path = path[len(base_url):]
    if not path.startswith("/"):
        path = "/" + path
    segments = path.split("/")
    segments = ["*" if seg in input_values else seg for seg in segments]
    return "/".join(segments)


def build_capability(
    result: DiscoveryResult,
    *,
    capability_id: str,
    name: str,
    target_app: sc.TargetAppProfile,
    allowlist: Allowlist,
    outcomes: list[sc.Outcome] | None = None,
) -> sc.Capability | None:
    if result.status != "done":
        print(f"Discovery run did not complete successfully (status={result.status}); nothing to record.")
        return None

    inputs: dict[str, sc.InputParam] = {}
    outputs: dict[str, sc.OutputField] = {}
    steps: list = []
    input_literal_values: set[str] = set()

    for s in result.steps:
        if not s.success:
            continue
        if s.excluded_from_artifact:
            # Login sequence -- never persisted (see loop.py). Replay
            # authenticates separately using its own held credentials.
            continue

        if s.action == "navigate":
            steps.append(sc.NavigateStep(step_id=s.step_id, url=s.tool_input["url"]))

        elif s.action == "click":
            steps.append(sc.ClickStep(
                step_id=s.step_id,
                target=sc.Locator(primary=sc.LocatorStrategy(
                    strategy=sc.LocatorStrategyKind.ROLE_NAME,
                    role=s.tool_input["role"], name=s.tool_input["name"],
                )),
            ))

        elif s.action in ("type", "select"):
            role, field_name, value = s.tool_input["role"], s.tool_input["name"], s.tool_input["value"]
            decision = s.param_decision or {}
            locator = sc.Locator(primary=sc.LocatorStrategy(
                strategy=sc.LocatorStrategyKind.ROLE_NAME, role=role, name=field_name,
            ))
            if decision.get("parameterize"):
                pname = decision["param_name"]
                ptype = sc.ParamType(decision.get("param_type") or _param_type(value))
                if pname not in inputs:
                    inputs[pname] = sc.InputParam(name=pname, type=ptype,
                                                   description=f"Value for field '{field_name}'")
                value_ref: sc.ValueRef = sc.InputRef(name=pname)
                input_literal_values.add(value)
            else:
                if looks_sensitive(value):
                    print(f'[REVIEW] Step {s.step_id}: literal value "{value}" for field "{field_name}" '
                          f'looks like it could be real data but is being stored as a fixed literal.')
                value_ref = _coerce_literal(value)

            step_cls = sc.TypeStep if s.action == "type" else sc.SelectStep
            steps.append(step_cls(step_id=s.step_id, target=locator, value=value_ref))

        elif s.action == "extract":
            decision = s.param_decision or {}
            if not decision.get("is_output"):
                continue  # an internal check, not a declared output -- omit from the artifact
            oname = decision["output_name"]
            outputs[oname] = sc.OutputField(name=oname, type=sc.ParamType.STRING,
                                             description=f"Extracted from '{s.tool_input['name']}'",
                                             produced_by_step=s.step_id)
            steps.append(sc.ExtractStep(
                step_id=s.step_id,
                target=sc.Locator(primary=sc.LocatorStrategy(
                    strategy=sc.LocatorStrategyKind.ROLE_NAME,
                    role=s.tool_input["role"], name=s.tool_input["name"],
                )),
                output=sc.OutputRef(name=oname),
            ))

    if not steps:
        print("No successful steps to record.")
        return None

    final_url = next((s.page_url_after for s in reversed(result.steps) if s.page_url_after), target_app.base_url)
    checkpoint_clauses = [sc.Condition(
        kind=sc.ConditionKind.URL_MATCH,
        url_pattern=_normalize_url(final_url, target_app.base_url, input_literal_values),
    )]
    last_extract_step = next((s for s in reversed(steps) if isinstance(s, sc.ExtractStep)), None)
    if last_extract_step:
        checkpoint_clauses.append(sc.Condition(kind=sc.ConditionKind.ELEMENT_PRESENT, locator=last_extract_step.target))
    checkpoint = (
        checkpoint_clauses[0] if len(checkpoint_clauses) == 1
        else sc.Condition(kind=sc.ConditionKind.ALL_OF, all_of=checkpoint_clauses)
    )

    print("\n=== Capability review ===")
    print(f"Name: {name}")
    print(f"Goal: {result.goal}")
    print(f"Inputs: {[i.name for i in inputs.values()] or 'none'}")
    print(f"Outputs: {[o.name for o in outputs.values()] or 'none'}")
    print(f"Steps: {len(steps)}")
    print(f"Checkpoint: {checkpoint.model_dump()}")
    is_irreversible = hc.ask_yes_no(
        "\nDoes this capability make a real, irreversible change (vs. a read-only lookup)?", default=True
    )
    risk_level = sc.RiskLevel.IRREVERSIBLE if is_irreversible else sc.RiskLevel.SAFE
    policy = sc.Policy(
        risk_level=risk_level,
        approval_state=sc.ApprovalState.DRAFT,
        allowed_scope=sc.AllowedScope(
            domains=allowlist.domains, routes=allowlist.routes, action_types=allowlist.action_types,
        ),
        idempotency=sc.Idempotency(on_retry=sc.RetryPolicy.ABORT_AND_ESCALATE) if is_irreversible else None,
    )

    capability = sc.Capability(
        metadata=sc.Metadata(
            capability_id=capability_id, name=name, created_from_goal=redact_secrets(result.goal),
            discovery_run_id=result.run_id, target_app=target_app,
        ),
        inputs=list(inputs.values()),
        outputs=list(outputs.values()),
        steps=steps,
        checkpoint=checkpoint,
        outcomes=outcomes or [],
        policy=policy,
    )

    if not hc.ask_yes_no("\nSave this artifact to /artifacts?", default=True):
        print("Discarded -- not saved.")
        return None

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTIFACTS_DIR / f"{capability_id}.json"
    out_path.write_text(capability.model_dump_json(indent=2))
    print(f"Saved: {out_path}")
    return capability
