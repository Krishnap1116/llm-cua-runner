"""Typed, versioned capability artifact schema.

This is the reusable, reviewable contract a discovery run produces and a
replay engine consumes. See /REPORT.md (Artifact schema) for the design
reasoning behind every field below.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class ParamType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"


class InputParam(BaseModel):
    """A typed input the caller (an AI agent) supplies per invocation."""

    name: str
    type: ParamType
    required: bool = True
    description: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None


class OutputField(BaseModel):
    """A typed piece of data the caller gets back."""

    name: str
    type: ParamType
    description: str = ""
    produced_by_step: str  # step_id of the ExtractStep that fills this


class InputRef(BaseModel):
    """A validated reference to a declared input param.

    Using a structured ref (instead of a bare "$name" string) means a
    dangling/typo'd reference fails when the artifact is loaded, not three
    steps into a live replay.
    """

    source: Literal["input"] = "input"
    name: str


class OutputRef(BaseModel):
    source: Literal["output"] = "output"
    name: str


class Literal_(BaseModel):
    """A fixed, non-parameterized value baked into the recorded step.

    Only ever used for values that are NOT user-supplied during discovery
    (e.g. a fixed dropdown choice like "Sub-Savings"). Anything that came
    from a discovery-time input field must be an InputRef instead -- the
    recorder enforces this by construction so no literal PII ends up
    persisted in the artifact.
    """

    source: Literal["literal"] = "literal"
    value: Union[str, float, bool]


ValueRef = Annotated[Union[InputRef, Literal_], Field(discriminator="source")]


# ---------------------------------------------------------------------------
# Locators -- how a step finds its target control
# ---------------------------------------------------------------------------

class LocatorStrategyKind(str, Enum):
    ROLE_NAME = "role_name"          # accessibility role + accessible name (primary)
    LABEL_TEXT = "label_text"        # associated <label> text
    TEXT = "text"                    # visible text content, scoped
    CSS_STRUCTURAL = "css_structural"  # positional CSS path (last resort)


class LocatorStrategy(BaseModel):
    strategy: LocatorStrategyKind
    role: Optional[str] = None      # e.g. "button", "textbox", "link"
    name: Optional[str] = None      # accessible name / label / visible text
    css: Optional[str] = None       # only for css_structural
    scope: Optional[str] = None     # optional containing region, e.g. "main table row 3"


class Locator(BaseModel):
    """Primary + ordered fallback chain, most robust first.

    Robustness reasoning (see REPORT.md): role+name survives hostile,
    non-semantic markup because it reads the accessibility tree rather
    than CSS structure. Fallbacks exist for controls that genuinely lack
    accessible roles/names on a given legacy page, and are ordered from
    "still fairly stable" to "brittle, last resort".
    """

    primary: LocatorStrategy
    fallback: list[LocatorStrategy] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Conditions -- used by checkpoints, outcomes, and step-level asserts
# ---------------------------------------------------------------------------

class ConditionKind(str, Enum):
    URL_MATCH = "url_match"
    ELEMENT_PRESENT = "element_present"   # scoped locator resolves to something
    TEXT_IN_REGION = "text_in_region"     # text match, scoped to a role/region (not full-page)
    STATUS_CODE = "status_code"
    ALL_OF = "all_of"                     # structured AND of sub-conditions


class Condition(BaseModel):
    kind: ConditionKind
    url_pattern: Optional[str] = None
    locator: Optional[Locator] = None
    region: Optional[LocatorStrategy] = None   # required with TEXT_IN_REGION
    text_pattern: Optional[str] = None
    status_code: Optional[int] = None
    all_of: Optional[list["Condition"]] = None

    @model_validator(mode="after")
    def _require_fields_for_kind(self) -> "Condition":
        need = {
            ConditionKind.URL_MATCH: ["url_pattern"],
            ConditionKind.ELEMENT_PRESENT: ["locator"],
            ConditionKind.TEXT_IN_REGION: ["region", "text_pattern"],
            ConditionKind.STATUS_CODE: ["status_code"],
            ConditionKind.ALL_OF: ["all_of"],
        }[self.kind]
        for field in need:
            if getattr(self, field) is None:
                raise ValueError(f"condition kind={self.kind} requires field '{field}'")
        return self


Condition.model_rebuild()


# ---------------------------------------------------------------------------
# Wait / retry policy
# ---------------------------------------------------------------------------

class WaitPolicy(BaseModel):
    timeout_ms: int = 5000
    retry_count: int = 1
    backoff_ms: int = 500


# ---------------------------------------------------------------------------
# Steps -- discriminated union by action
# ---------------------------------------------------------------------------

class StepBase(BaseModel):
    step_id: str
    wait_policy: WaitPolicy = Field(default_factory=WaitPolicy)
    is_commit_step: bool = False  # the irreversible action itself (e.g. final "Confirm")
                                   # -- assisted fallback is never allowed here, no exceptions


class NavigateStep(StepBase):
    action: Literal["navigate"] = "navigate"
    url: str


class ClickStep(StepBase):
    action: Literal["click"] = "click"
    target: Locator


class TypeStep(StepBase):
    action: Literal["type"] = "type"
    target: Locator
    value: ValueRef


class SelectStep(StepBase):
    action: Literal["select"] = "select"
    target: Locator
    value: ValueRef


class ExtractStep(StepBase):
    """Reads data from the page. Failure to resolve `target` is ALWAYS a
    hard failure -- extraction never silently returns null/empty. Handing
    an AI agent a wrong balance with a success flag is worse than a loud
    crash; this rule is enforced by the replay engine, not optional.
    """

    action: Literal["extract"] = "extract"
    target: Locator
    output: OutputRef


class AssertStep(StepBase):
    """A step-level checkpoint: confirms this step actually worked before
    moving on, instead of only discovering a silent failure at the end."""

    action: Literal["assert"] = "assert"
    condition: Condition


Step = Annotated[
    Union[NavigateStep, ClickStep, TypeStep, SelectStep, ExtractStep, AssertStep],
    Field(discriminator="action"),
]


# ---------------------------------------------------------------------------
# Outcomes -- declarative runtime-error taxonomy
# ---------------------------------------------------------------------------

class OutcomeKind(str, Enum):
    BUSINESS_OUTCOME = "business_outcome"  # legitimate result, not a crash (e.g. "not found")
    RECOVERABLE = "recoverable"            # replay can act and continue (e.g. re-login)
    HARD_FAILURE = "hard_failure"          # stop, surface a clear debuggable error


class RecoveryAction(str, Enum):
    REAUTHENTICATE_AND_RESUME = "reauthenticate_and_resume"
    WAIT_AND_RETRY = "wait_and_retry"
    NONE = "none"


class Outcome(BaseModel):
    """One recognized runtime condition. `outcomes` is evaluated in order;
    the FIRST match wins, so ambiguity between overlapping detectors has a
    defined resolution instead of undefined behavior.
    """

    name: str
    kind: OutcomeKind
    detect: Condition
    recovery: RecoveryAction = RecoveryAction.NONE
    business_result: Optional[str] = None  # e.g. "member_not_found"
    message: str = ""


# ---------------------------------------------------------------------------
# Policy -- safety, risk, idempotency, allowlist scope
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    SAFE = "safe"                # read-only / reversible
    IRREVERSIBLE = "irreversible"


class ApprovalState(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"


class RetryPolicy(str, Enum):
    SAFE_TO_RETRY = "safe_to_retry"
    ABORT_AND_ESCALATE = "abort_and_escalate"


class Idempotency(BaseModel):
    pre_check: Optional[Condition] = None  # e.g. "does this nickname already exist?"
    on_retry: RetryPolicy = RetryPolicy.ABORT_AND_ESCALATE


class AllowedScope(BaseModel):
    domains: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)   # glob-style path patterns
    action_types: list[str] = Field(default_factory=list)  # subset of Step.action values


class Policy(BaseModel):
    risk_level: RiskLevel
    approval_state: ApprovalState = ApprovalState.DRAFT
    allowed_scope: AllowedScope
    idempotency: Optional[Idempotency] = None
    allow_assisted_fallback: bool = False  # opt-in: bounded, single-step LLM recovery on replay
                                            # locator failure (never on an is_commit_step)

    @model_validator(mode="after")
    def _irreversible_needs_idempotency(self) -> "Policy":
        if self.risk_level == RiskLevel.IRREVERSIBLE and self.idempotency is None:
            raise ValueError(
                "irreversible capabilities must declare an idempotency policy"
            )
        return self


# ---------------------------------------------------------------------------
# Multi-tenant reuse seam (design-only for this project, see REPORT.md 3.7)
# ---------------------------------------------------------------------------

class TenantOverride(BaseModel):
    base_url: Optional[str] = None
    locator_overrides: dict[str, Locator] = Field(default_factory=dict)  # step_id -> Locator


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TargetAppProfile(BaseModel):
    vendor: str
    app_name: str
    base_url: str
    app_version: Optional[str] = None


class Metadata(BaseModel):
    capability_id: str
    name: str
    schema_version: str = SCHEMA_VERSION
    version: int = 1
    created_from_goal: str
    discovery_run_id: str  # resolves to evidence/runs/<discovery_run_id>/
    target_app: TargetAppProfile


# ---------------------------------------------------------------------------
# Top-level Capability artifact
# ---------------------------------------------------------------------------

class Capability(BaseModel):
    metadata: Metadata
    inputs: list[InputParam] = Field(default_factory=list)
    outputs: list[OutputField] = Field(default_factory=list)
    steps: list[Step]
    checkpoint: Condition
    outcomes: list[Outcome] = Field(default_factory=list)
    policy: Policy
    tenant_overrides: dict[str, TenantOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_refs(self) -> "Capability":
        input_names = {p.name for p in self.inputs}
        output_names = {o.name for o in self.outputs}
        step_ids = {s.step_id for s in self.steps}

        if len(step_ids) != len(self.steps):
            raise ValueError("duplicate step_id in steps[]")

        for step in self.steps:
            if isinstance(step, (TypeStep, SelectStep)):
                if isinstance(step.value, InputRef) and step.value.name not in input_names:
                    raise ValueError(
                        f"step {step.step_id} references undeclared input '{step.value.name}'"
                    )
            if isinstance(step, ExtractStep) and step.output.name not in output_names:
                raise ValueError(
                    f"step {step.step_id} references undeclared output '{step.output.name}'"
                )

        for out in self.outputs:
            if out.produced_by_step not in step_ids:
                raise ValueError(
                    f"output '{out.name}' references unknown step_id '{out.produced_by_step}'"
                )

        return self
