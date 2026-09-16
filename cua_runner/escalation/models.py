"""Structured record of one escalation event -- what the brief calls an
'intervention request': enough context for a human to act on it, and a
record of what they did, for evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InterventionRequest:
    escalation_id: str
    source: str              # "discovery" | "replay"
    capability_or_goal: str  # capability_id (replay) or the goal text (discovery)
    step_id: Optional[str]
    reason: str
    url: str
    screenshot_path: str
    accessibility_snapshot: str = ""
    human_notes: str = ""
    resolved: bool = False
