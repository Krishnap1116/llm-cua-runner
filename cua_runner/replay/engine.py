"""Deterministic replay (Section 3.3): runs a saved Capability artifact
against a live surface with NO LLM in the decision loop (assisted
fallback is the one bounded, opt-in exception -- see assisted_fallback.py).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from cua_runner import schema as sc
from cua_runner.replay.assisted_fallback import suggest_replacement_target
from cua_runner.replay.auth import AuthenticationError, ensure_authenticated
from cua_runner.replay.conditions import ReplayContext, evaluate
from cua_runner.replay.locator_resolver import LocatorResolutionError, resolve
from cua_runner.replay.results import DebugInfo, ReplayResult, ReplayStatus
from cua_runner.guardrails.policy import Allowlist, GuardrailViolation, resolve_url
from cua_runner.agent.redaction import redact_financial_data, redact_secrets
from cua_runner.escalation.handoff import escalate


class ParamValidationError(Exception):
    pass


def load_capability(path: str) -> sc.Capability:
    return sc.Capability.model_validate_json(Path(path).read_text())


def validate_params(cap: sc.Capability, params: dict) -> None:
    for p in cap.inputs:
        if p.required and p.name not in params:
            raise ParamValidationError(f"missing required input '{p.name}'")
        if p.name not in params:
            continue
        value = params[p.name]
        if p.type == sc.ParamType.NUMBER and not isinstance(value, (int, float)):
            raise ParamValidationError(f"input '{p.name}' must be a number, got {type(value).__name__}")
        if p.type == sc.ParamType.STRING and not isinstance(value, str):
            raise ParamValidationError(f"input '{p.name}' must be a string, got {type(value).__name__}")
        if p.max_length and isinstance(value, str) and len(value) > p.max_length:
            raise ParamValidationError(f"input '{p.name}' exceeds max_length={p.max_length}")
        if p.min is not None and isinstance(value, (int, float)) and value < p.min:
            raise ParamValidationError(f"input '{p.name}' is below minimum {p.min}")


def _resolve_value(value_ref: sc.ValueRef, params: dict):
    if isinstance(value_ref, sc.InputRef):
        return params[value_ref.name]
    return value_ref.value


def _check_outcomes(outcomes: list[sc.Outcome], ctx: ReplayContext) -> Optional[sc.Outcome]:
    for outcome in outcomes:
        if evaluate(outcome.detect, ctx):
            return outcome
    return None


def _execute_step(page: Page, step: sc.Step, params: dict, outputs: dict, result: ReplayResult,
                   allowlist: Allowlist, base_url: str) -> None:
    timeout_ms = step.wait_policy.timeout_ms

    # Guardrails: enforced BEFORE execution on every single step, using the
    # allowlist embedded in THIS artifact's own policy -- not just at
    # discovery time. An artifact is a saved, edited-by-humans file; it
    # must not be trusted to only ever contain in-scope actions.
    allowlist.check_action_type(step.action)
    if isinstance(step, sc.NavigateStep):
        allowlist.check_navigate(resolve_url(step.url, base_url))
    else:
        allowlist.check_navigate(page.url)  # whatever page we're about to act on must still be in-scope

    if isinstance(step, sc.NavigateStep):
        page.goto(step.url, wait_until="load", timeout=timeout_ms)
        return

    if isinstance(step, sc.ClickStep):
        r = resolve(page, step.target, timeout_ms=timeout_ms)
        if r.used_fallback:
            result.drift_signals.append(step.step_id)
        r.locator.first.click(timeout=timeout_ms)
        page.wait_for_load_state("load", timeout=timeout_ms)
        return

    if isinstance(step, (sc.TypeStep, sc.SelectStep)):
        r = resolve(page, step.target, timeout_ms=timeout_ms)
        if r.used_fallback:
            result.drift_signals.append(step.step_id)
        value = _resolve_value(step.value, params)
        if isinstance(step, sc.TypeStep):
            r.locator.first.fill(str(value), timeout=timeout_ms)
        else:
            r.locator.first.select_option(label=str(value), timeout=timeout_ms)
        return

    if isinstance(step, sc.ExtractStep):
        r = resolve(page, step.target, for_value=True, timeout_ms=timeout_ms)
        if r.used_fallback:
            result.drift_signals.append(step.step_id)
        text = r.locator.inner_text(timeout=timeout_ms).strip()
        outputs[step.output.name] = text
        return

    if isinstance(step, sc.AssertStep):
        assert_ctx = ReplayContext(page=page)
        if not evaluate(step.condition, assert_ctx):
            raise LocatorResolutionError(f"assertion failed for step {step.step_id}")
        return


def _evidence_dir(capability_id: str) -> Path:
    run_id = time.strftime("replay_%Y%m%d_%H%M%S")
    d = Path(__file__).resolve().parents[2] / "evidence" / "replays" / f"{run_id}_{capability_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _finish(evidence_dir: Path, artifact_path: str, params: dict, result: ReplayResult,
            page: Page | None) -> ReplayResult:
    if page is not None:
        try:
            page.screenshot(path=str(evidence_dir / "final_state.png"))
        except Exception:
            pass
    # Redaction applies to INCIDENTAL exposure (free-text messages/debug
    # info that might happen to quote page content) -- never to `outputs`,
    # which is the capability's deliberate, declared result. Redacting the
    # thing the caller explicitly asked for would defeat the evidence's
    # whole purpose (Section 3.5: enough evidence to debug a run).
    def _sanitize(text: str) -> str:
        return redact_financial_data(redact_secrets(text))

    payload = {
        "artifact": artifact_path,
        "params": params,
        "status": result.status.value,
        "message": _sanitize(result.message),
        "outputs": result.outputs,
        "business_result": result.business_result,
        "debug": ({**asdict(result.debug), "observed": _sanitize(result.debug.observed)}
                  if result.debug else None),
        "retry_guidance": result.retry_guidance,
        "drift_signals": result.drift_signals,
        "assisted_fallback_used": result.assisted_fallback_used,
    }
    (evidence_dir / "result.json").write_text(json.dumps(payload, indent=2))
    return result


def replay(
    artifact_path: str,
    params: dict,
    *,
    base_url_override: str | None = None,
    force_unattended: bool = False,
    headless: bool = True,
    allow_escalation: bool = False,
) -> ReplayResult:
    cap = load_capability(artifact_path)
    evidence_dir = _evidence_dir(cap.metadata.capability_id)

    try:
        validate_params(cap, params)
    except ParamValidationError as e:
        return _finish(evidence_dir, artifact_path, params, ReplayResult(
            status=ReplayStatus.HARD_FAILURE, message=str(e),
            debug=DebugInfo(step_id=None, expected="valid input parameters", observed=str(e)),
        ), None)

    if (cap.policy.risk_level == sc.RiskLevel.IRREVERSIBLE
            and cap.policy.approval_state == sc.ApprovalState.DRAFT
            and not force_unattended):
        return _finish(evidence_dir, artifact_path, params, ReplayResult(
            status=ReplayStatus.HARD_FAILURE,
            message="This capability is IRREVERSIBLE and still in DRAFT approval state -- "
                    "unattended replay is blocked until a human reviews and approves it.",
            debug=DebugInfo(step_id=None, expected="policy.approval_state == approved",
                             observed=cap.policy.approval_state.value),
        ), None)

    base_url = base_url_override or cap.metadata.target_app.base_url
    allowlist = Allowlist(
        domains=cap.policy.allowed_scope.domains,
        routes=cap.policy.allowed_scope.routes,
        action_types=cap.policy.allowed_scope.action_types,
    )
    result = ReplayResult(status=ReplayStatus.SUCCESS)
    outputs: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        ctx = ReplayContext(page=page)
        escalation_attempts = 0
        MAX_ESCALATIONS = 1

        def _track_status(response):
            if response.request.resource_type == "document":
                ctx.last_status_code = response.status
        page.on("response", _track_status)

        try:
            ensure_authenticated(page, base_url)
        except AuthenticationError as e:
            handled = False
            if allow_escalation and escalation_attempts < MAX_ESCALATIONS:
                escalation_attempts += 1
                request = escalate(page, source="replay", capability_or_goal=cap.metadata.capability_id,
                                    step_id=None, reason=f"Authentication failed: {e}")
                if request.resolved:
                    try:
                        ensure_authenticated(page, base_url)
                        handled = True
                    except AuthenticationError:
                        pass
            if not handled:
                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                    status=ReplayStatus.HARD_FAILURE, message=str(e),
                    debug=DebugInfo(step_id=None, expected="successful authentication", observed=str(e)),
                ), page)

        if cap.policy.idempotency and cap.policy.idempotency.pre_check:
            if evaluate(cap.policy.idempotency.pre_check, ctx):
                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                    status=ReplayStatus.BUSINESS_OUTCOME, business_result="already_done",
                    message="Idempotency pre-check indicates this action was already performed.",
                ), page)

        reauth_attempted = False
        step_idx = 0
        while step_idx < len(cap.steps):
            step = cap.steps[step_idx]
            try:
                _execute_step(page, step, params, outputs, result, allowlist, base_url)
            except GuardrailViolation as e:
                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                    status=ReplayStatus.HARD_FAILURE,
                    message=f"Guardrail violation -- replay blocked: {e}",
                    debug=DebugInfo(step_id=step.step_id, expected="action within allowed_scope", observed=str(e)),
                    retry_guidance="abort_and_escalate",
                ), page)
            except Exception as e:
                if (cap.policy.allow_assisted_fallback and not step.is_commit_step
                        and hasattr(step, "target") and isinstance(e, LocatorResolutionError)):
                    suggestion = suggest_replacement_target(page, step, step.target.primary)
                    if suggestion:
                        patched = step.model_copy(deep=True)
                        patched.target = sc.Locator(primary=sc.LocatorStrategy(
                            strategy=sc.LocatorStrategyKind.ROLE_NAME, role=suggestion["role"], name=suggestion["name"],
                        ))
                        try:
                            _execute_step(page, patched, params, outputs, result, allowlist, base_url)
                            result.assisted_fallback_used.append(step.step_id)
                            step_idx += 1
                            continue
                        except Exception:
                            pass

                matched = _check_outcomes(cap.outcomes, ctx)
                if matched is not None:
                    if matched.kind == sc.OutcomeKind.BUSINESS_OUTCOME:
                        return _finish(evidence_dir, artifact_path, params, ReplayResult(
                            status=ReplayStatus.BUSINESS_OUTCOME, business_result=matched.business_result,
                            message=matched.message,
                        ), page)
                    if matched.kind == sc.OutcomeKind.HARD_FAILURE:
                        return _finish(evidence_dir, artifact_path, params, ReplayResult(
                            status=ReplayStatus.HARD_FAILURE, message=matched.message,
                            debug=DebugInfo(step_id=step.step_id, expected=step.action, observed=matched.name),
                            retry_guidance=(cap.policy.idempotency.on_retry.value if cap.policy.idempotency else None),
                        ), page)
                    if matched.kind == sc.OutcomeKind.RECOVERABLE:
                        if matched.recovery == sc.RecoveryAction.REAUTHENTICATE_AND_RESUME and not reauth_attempted:
                            reauth_attempted = True
                            try:
                                ensure_authenticated(page, base_url)
                            except AuthenticationError as auth_e:
                                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                                    status=ReplayStatus.HARD_FAILURE, message=str(auth_e),
                                    debug=DebugInfo(step_id=step.step_id, expected="re-authentication", observed=str(auth_e)),
                                ), page)
                            step_idx = 0
                            outputs.clear()
                            continue
                        if matched.recovery == sc.RecoveryAction.WAIT_AND_RETRY:
                            time.sleep(step.wait_policy.backoff_ms / 1000)
                            continue
                    if allow_escalation and escalation_attempts < MAX_ESCALATIONS:
                        escalation_attempts += 1
                        request = escalate(page, source="replay", capability_or_goal=cap.metadata.capability_id,
                                            step_id=step.step_id, reason=f"Recovery exhausted: {matched.message}")
                        if request.resolved:
                            continue  # retry the SAME step_idx for real -- its own checks verify state
                    return _finish(evidence_dir, artifact_path, params, ReplayResult(
                        status=ReplayStatus.HARD_FAILURE, message=matched.message,
                        debug=DebugInfo(step_id=step.step_id, expected=step.action, observed="recovery exhausted"),
                    ), page)

                if allow_escalation and escalation_attempts < MAX_ESCALATIONS:
                    escalation_attempts += 1
                    request = escalate(page, source="replay", capability_or_goal=cap.metadata.capability_id,
                                        step_id=step.step_id, reason=f"Step failed, no known outcome matched: {e}")
                    if request.resolved:
                        continue  # retry the SAME step_idx for real -- its own checks verify state

                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                    status=ReplayStatus.HARD_FAILURE,
                    message=f"Step {step.step_id} failed and matched no known outcome.",
                    debug=DebugInfo(step_id=step.step_id, expected=step.action, observed=str(e)),
                    retry_guidance=(cap.policy.idempotency.on_retry.value if cap.policy.idempotency else None),
                ), page)

            matched = _check_outcomes(cap.outcomes, ctx)
            if matched is not None:
                if matched.kind == sc.OutcomeKind.BUSINESS_OUTCOME:
                    return _finish(evidence_dir, artifact_path, params, ReplayResult(
                        status=ReplayStatus.BUSINESS_OUTCOME, business_result=matched.business_result,
                        message=matched.message,
                    ), page)
                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                    status=ReplayStatus.HARD_FAILURE, message=matched.message,
                    debug=DebugInfo(step_id=step.step_id, expected=step.action, observed=matched.name),
                ), page)

            step_idx += 1

        if not evaluate(cap.checkpoint, ctx):
            checkpoint_ok = False
            if allow_escalation and escalation_attempts < MAX_ESCALATIONS:
                escalation_attempts += 1
                request = escalate(page, source="replay", capability_or_goal=cap.metadata.capability_id,
                                    step_id="checkpoint", reason="All steps ran, but the expected end state was not reached.")
                if request.resolved:
                    checkpoint_ok = evaluate(cap.checkpoint, ctx)  # re-check for real, don't just trust the human's say-so
            if not checkpoint_ok:
                return _finish(evidence_dir, artifact_path, params, ReplayResult(
                    status=ReplayStatus.HARD_FAILURE,
                    message="All steps completed, but the expected end state (checkpoint) was not reached.",
                    debug=DebugInfo(step_id="checkpoint", expected=str(cap.checkpoint.model_dump()), observed=page.url),
                ), page)

        result.status = ReplayStatus.SUCCESS
        result.outputs = outputs
        result.message = "Capability completed successfully."
        return _finish(evidence_dir, artifact_path, params, result, page)
