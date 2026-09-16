"""The replay result contract (Section 3.3): a caller must be able to
tell apart success, a legitimate business outcome, and a hard failure --
never conflate the three.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    HARD_FAILURE = "hard_failure"


@dataclass
class DebugInfo:
    step_id: Optional[str]
    expected: str
    observed: str


@dataclass
class ReplayResult:
    status: ReplayStatus
    outputs: dict = field(default_factory=dict)
    business_result: Optional[str] = None
    message: str = ""
    debug: Optional[DebugInfo] = None
    retry_guidance: Optional[str] = None  # e.g. "abort_and_escalate" for irreversible hard failures
    assisted_fallback_used: list = field(default_factory=list)  # step_ids where it fired
    drift_signals: list = field(default_factory=list)  # step_ids where a fallback locator was needed
