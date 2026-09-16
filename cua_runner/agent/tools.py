"""The fixed action vocabulary the discovery agent may use.

Deliberately identical to the artifact schema's step actions (navigate,
click, type, select, extract, plus the meta-actions done/stuck) so that
recording a run is a direct transcription, not a reinterpretation. This
is also the safety boundary: the agent is mechanically incapable of doing
anything outside this list, no matter what it decides.
"""
from __future__ import annotations

from playwright.sync_api import Page

TOOL_SCHEMAS = [
    {
        "name": "navigate",
        "description": "Go to a URL (relative paths are resolved against the current site).",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": "Click a control identified by its accessible role and name, e.g. role='button', name='Open Sub-Account'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["role", "name"],
        },
    },
    {
        "name": "type",
        "description": "Type text into a field identified by its accessible role and name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["role", "name", "value"],
        },
    },
    {
        "name": "select",
        "description": "Choose an option from a dropdown (role='combobox') identified by its accessible name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["role", "name", "value"],
        },
    },
    {
        "name": "extract",
        "description": "Read a value from the page. Identify the target by its STABLE LABEL text (e.g. role='cell', name='Account Number'), never by the value itself -- the value changes every time this is replayed, so locating by it would break on the next run. For a legacy label/value table row, name the label cell; the adjacent value cell is read automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "output_name": {"type": "string"},
            },
            "required": ["role", "name", "output_name"],
        },
    },
    {
        "name": "done",
        "description": "Call this when the goal has been fully achieved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "outputs": {"type": "object"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "stuck",
        "description": "Call this if you cannot safely make progress -- do not guess or force an action.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]



class ActionError(Exception):
    """A tool call failed to execute against the live page."""


def value_locator_for(label_locator, role: str):
    """Legacy label/value table pattern: the label cell is stable across
    replays, but the adjacent value cell's TEXT changes every time (a new
    account number, a different balance) -- so we locate by the label and
    read its sibling, never by the value itself (which wouldn't exist on
    the next run with different inputs). Shared between discovery and
    replay so both resolve extraction targets identically."""
    if role == "cell":
        return label_locator.locator("xpath=./following-sibling::*[1]")
    return label_locator


# Standard HTML autocomplete tokens that mark a field as a login credential,
# per the WHATWG spec -- used by real login forms across the entire web, not
# something specific to this app. This lets us detect "this field must never
# be persisted into an artifact" generically, on any surface.
_CREDENTIAL_AUTOCOMPLETE_VALUES = {"username", "current-password", "new-password", "one-time-code"}


def is_credential_field(page: Page, role: str, name: str) -> bool:
    try:
        locator = page.get_by_role(role, name=name, exact=True)
        autocomplete = locator.get_attribute("autocomplete", timeout=2000)
        return (autocomplete or "").strip().lower() in _CREDENTIAL_AUTOCOMPLETE_VALUES
    except Exception:
        return False


def execute(page: Page, tool_name: str, tool_input: dict, base_url: str) -> dict:
    """Executes one tool call against the live page. Returns extracted
    data for 'extract'; raises ActionError with a clear message on
    failure so the loop can feed it back to the model (or count it toward
    the dead-end detector)."""
    try:
        if tool_name == "navigate":
            url = tool_input["url"]
            if url.startswith("/"):
                url = base_url.rstrip("/") + url
            page.goto(url, wait_until="load", timeout=5000)
            return {}

        if tool_name == "click":
            page.get_by_role(tool_input["role"], name=tool_input["name"], exact=True).click(timeout=5000)
            page.wait_for_load_state("load", timeout=5000)
            return {}

        if tool_name == "type":
            page.get_by_role(tool_input["role"], name=tool_input["name"], exact=True).fill(
                tool_input["value"], timeout=5000
            )
            return {}

        if tool_name == "select":
            page.get_by_role(tool_input["role"], name=tool_input["name"], exact=True).select_option(
                label=tool_input["value"], timeout=5000
            )
            return {}

        if tool_name == "extract":
            role, name = tool_input["role"], tool_input["name"]
            locator = value_locator_for(page.get_by_role(role, name=name, exact=True), role)
            text = locator.inner_text(timeout=5000).strip()
            return {"output_name": tool_input["output_name"], "value": text}

        raise ActionError(f"unknown tool '{tool_name}'")
    except ActionError:
        raise
    except Exception as e:
        raise ActionError(f"{tool_name}({tool_input}) failed: {e}")
