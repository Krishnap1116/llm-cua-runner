"""Hand-authored outcomes for the 'open savings sub-account' capability.

Deliberately NOT discovered by the LLM: a single happy-path discovery run
never encounters these by definition. These are authored directly by a
human reviewer with real domain knowledge of the mock app's business
rules -- the same way an on-call runbook is written from known failure
modes, not by fuzzing every path first. See REPORT.md (Cuts) for why.

Evaluated in order; the FIRST match wins.
"""
from cua_runner import schema as sc

ALERT_REGION = sc.LocatorStrategy(strategy=sc.LocatorStrategyKind.ROLE_NAME, role="alert")

OPEN_SUBACCOUNT_OUTCOMES = [
    sc.Outcome(
        name="server_error",
        kind=sc.OutcomeKind.HARD_FAILURE,
        detect=sc.Condition(kind=sc.ConditionKind.STATUS_CODE, status_code=500),
        message="The application returned an unexpected server error.",
    ),
    sc.Outcome(
        name="session_expired",
        kind=sc.OutcomeKind.RECOVERABLE,
        recovery=sc.RecoveryAction.REAUTHENTICATE_AND_RESUME,
        detect=sc.Condition(kind=sc.ConditionKind.URL_MATCH, url_pattern="/login"),
        message="The session expired mid-flow; re-authenticate and resume.",
    ),
    sc.Outcome(
        name="member_not_found",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="not_found",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="No member found"),
        message="No member exists with the given ID.",
    ),
    sc.Outcome(
        name="member_restricted",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="restricted",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="restricted"),
        message="The member's account is restricted; the action was not attempted.",
    ),
    sc.Outcome(
        name="member_closed",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="closed",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="closed"),
        message="The member's account is closed; the action was not attempted.",
    ),
    sc.Outcome(
        name="record_locked",
        kind=sc.OutcomeKind.RECOVERABLE,
        recovery=sc.RecoveryAction.WAIT_AND_RETRY,
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="locked by another session"),
        message="The record is locked by another session; safe to retry shortly.",
    ),
    sc.Outcome(
        name="subaccount_cap_reached",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="cap_reached",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="maximum number"),
        message="This member already has the maximum number of sub-accounts.",
    ),
    sc.Outcome(
        name="validation_error",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="validation_error",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="must be at least"),
        message="The submitted deposit did not meet the minimum requirement.",
    ),
    sc.Outcome(
        name="duplicate_nickname",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="validation_error",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="already exists"),
        message="A sub-account with that nickname already exists for this member.",
    ),
]

# Outcomes for the 'look up member savings balance' capability. A
# read-only lookup only has one realistic business outcome: the member
# doesn't exist. Status (restricted/closed/locked) blocks WRITE actions
# in this app, not viewing a balance, so none of those apply here.
LOOKUP_BALANCE_OUTCOMES = [
    sc.Outcome(
        name="member_not_found",
        kind=sc.OutcomeKind.BUSINESS_OUTCOME,
        business_result="not_found",
        detect=sc.Condition(kind=sc.ConditionKind.TEXT_IN_REGION, region=ALERT_REGION, text_pattern="No member found"),
        message="No member exists with the given ID.",
    ),
]
