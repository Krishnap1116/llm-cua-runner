"""Shared 'pause, ask a human one question, wait, resume' primitive.

This is the same underlying seam used for (a) parameterization prompts
during discovery and (b) full escalation/handoff during replay (Section
3.6) -- built once here, reused in both places.
"""
from __future__ import annotations


def _looks_like_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


class NoTTYError(Exception):
    """No interactive terminal is attached (e.g. an unattended/scripted
    run). Callers must stop immediately with a safe default -- never
    retry/recurse on this, since every subsequent input() call would
    raise it again and recurse forever."""


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, OSError):
        # Different non-interactive contexts signal "no stdin" differently:
        # EOFError in a plain piped/closed-stdin process, OSError when
        # pytest (or similar test runners/CI) captures stdin outright.
        # Both mean the same thing here -- fail safe, never crash.
        raise NoTTYError()


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = _input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def ask_text(prompt: str, default: str = "") -> str:
    raw = _input(f"{prompt}{f' (suggested: {default})' if default else ''}: ").strip()
    return raw or default


def confirm_decision(summary: str) -> bool:
    """A second, explicit confirmation after an initial choice -- guards
    against a single accidental keypress committing a wrong, and since
    field decisions get reused silently on every future recording, a
    mistake here would otherwise propagate quietly."""
    print(f"  -> {summary}")
    return ask_yes_no("  Confirm this?", default=False)


def ask_parameterize(role: str, name: str, value: str) -> tuple[bool, str | None, str | None]:
    """Returns (should_parameterize, param_name_or_None, param_type_or_None).
    Fails safe to (False, None, None) -- always fixed -- if no terminal
    is attached, rather than crashing or looping an in-progress,
    API-metered discovery run. The fallback is logged clearly so it's
    caught in the mandatory artifact review step."""
    try:
        print(f"\n[PARAMETERIZE?] About to type/select \"{value}\" into "
              f"field labeled \"{name}\" (role={role})")
        make_param = ask_yes_no("  Make this a reusable input parameter for future replays?", default=False)
        if not make_param:
            if confirm_decision(f'This field will always be FIXED to "{value}".'):
                return False, None, None
            return ask_parameterize(role, name, value)  # they backed out, ask again

        suggested = name.lower().replace(" ", "_")
        param_name = ask_text("  Parameter name", default=suggested)
        # Type is asked explicitly rather than guessed from this one example
        # value -- a member ID that happens to look numeric ("10234") is
        # still semantically a string identifier, not a quantity, and a
        # naive float-parse heuristic gets this wrong silently.
        looks_numeric = _looks_like_number(value)
        param_type = "number" if ask_yes_no(
            f'  Is "{param_name}" a NUMERIC quantity (supports min/max, arithmetic) rather than an identifier/text?',
            default=looks_numeric,
        ) else "string"
        if confirm_decision(f'This field becomes reusable parameter "{param_name}" (type: {param_type}).'):
            return True, param_name, param_type
        return ask_parameterize(role, name, value)
    except NoTTYError:
        print(f'  [NO TTY] Defaulting field "{name}" to FIXED -- review this decision manually.')
        return False, None, None


def ask_output(role: str, name: str) -> tuple[bool, str | None]:
    """Returns (is_output, output_name_or_None). Same fail-safe behavior
    as ask_parameterize."""
    try:
        print(f"\n[OUTPUT?] About to read the value from \"{name}\" (role={role})")
        make_output = ask_yes_no("  Capture this as a named output returned to the caller?", default=False)
        if not make_output:
            if confirm_decision("This value will NOT be captured as an output."):
                return False, None
            return ask_output(role, name)

        suggested = name.lower().replace(" ", "_")
        output_name = ask_text("  Output name", default=suggested)
        if confirm_decision(f'This value becomes named output "{output_name}".'):
            return True, output_name
        return ask_output(role, name)
    except NoTTYError:
        print(f'  [NO TTY] Defaulting "{name}" to NOT captured as an output -- review manually.')
        return False, None
