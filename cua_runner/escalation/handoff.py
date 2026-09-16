"""Human escalation & handoff (Section 3.6).

Pause automation, give a human context and control of the SAME live
session (a real, visible browser window they interact with directly --
not a fresh one, not a transferred/serialized copy), then hand control
back. This module owns the "who is in control" state transition and the
intervention-request evidence trail; loop.py and engine.py call into it
at their respective trigger points and decide what "resume" means for
their own execution model (continuing the LLM loop vs. retrying a step).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import Page

from cua_runner.agent import human_checkpoint as hc
from cua_runner.agent.perception import perceive
from cua_runner.escalation.models import InterventionRequest


def _evidence_dir(escalation_id: str) -> Path:
    d = Path(__file__).resolve().parents[2] / "evidence" / "escalations" / escalation_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def escalate(page: Page, *, source: str, capability_or_goal: str, step_id: str | None, reason: str) -> InterventionRequest:
    escalation_id = time.strftime("escalation_%Y%m%d_%H%M%S")
    evidence_dir = _evidence_dir(escalation_id)
    screenshot_path = str(evidence_dir / "at_escalation.png")

    try:
        page.screenshot(path=screenshot_path)
    except Exception:
        pass
    try:
        snapshot = perceive(page)["accessibility_tree"]
    except Exception:
        snapshot = ""

    request = InterventionRequest(
        escalation_id=escalation_id, source=source, capability_or_goal=capability_or_goal,
        step_id=step_id, reason=reason, url=getattr(page, "url", ""),
        screenshot_path=screenshot_path, accessibility_snapshot=snapshot,
    )

    print("\n" + "=" * 70)
    print("CONTROL TRANSFERRED: AUTOMATION -> HUMAN OPERATOR")
    print("=" * 70)
    print(f"Source: {source}")
    print(f"Capability/goal: {capability_or_goal}")
    print(f"Step: {step_id}")
    print(f"Reason: {reason}")
    print(f"Current URL: {request.url}")
    print(f"Evidence: {evidence_dir}")
    print("\nA live browser window is open at the point where automation stopped.")
    print("Please resolve the issue directly in that window, then respond below.")

    try:
        resolved = hc.ask_yes_no("\nHave you finished and is it safe for automation to resume?", default=False)
        notes = hc.ask_text("Briefly describe what you did (for the evidence record)", default="")
    except hc.NoTTYError:
        print("[NO TTY] No human operator is attached -- escalation cannot be resolved automatically.")
        resolved, notes = False, "(no interactive operator available)"

    request.resolved = resolved
    request.human_notes = notes

    print("\n" + "=" * 70)
    print(f"CONTROL RETURNED: HUMAN OPERATOR -> AUTOMATION (resolved={resolved})")
    print("=" * 70 + "\n")

    (evidence_dir / "intervention.json").write_text(json.dumps(request.__dict__, indent=2))
    return request
