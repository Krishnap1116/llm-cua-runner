"""Persisted memory of parameterization decisions, keyed by (role, name).

Once a field pattern has been decided ("Initial Deposit" -> parameterize,
"Username" -> always fixed), every future recording -- even for a totally
different capability -- reuses that decision silently instead of asking
again. Only a genuinely new field pattern triggers a human prompt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "field_decisions.json"

# Heuristics for the PII safety-net: values that *look like* they might be
# real identifiers/amounts even if a human said "keep this fixed". This is
# a backstop against an accidental wrong answer, not a replacement for it.
_LOOKS_LIKE_ID = re.compile(r"^\d{4,}$")
_LOOKS_LIKE_MONEY = re.compile(r"^\$?\d+(\.\d{2})?$")


def _key(role: str, name: str) -> str:
    return f"{role}::{name}"


class FieldMemory:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            self._data = json.loads(path.read_text())

    def lookup(self, role: str, name: str) -> Optional[dict]:
        return self._data.get(_key(role, name))

    def remember(self, role: str, name: str, decision: dict) -> None:
        self._data[_key(role, name)] = decision
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))

    def forget(self, role: str, name: str) -> None:
        self._data.pop(_key(role, name), None)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))


def looks_sensitive(value: str) -> bool:
    """True if a literal value structurally resembles an identifier or a
    dollar amount -- used to flag a possibly-wrong 'keep fixed' answer
    before an artifact is finalized, since that's the higher-risk mistake
    direction (real data baked into a saved artifact)."""
    v = value.strip()
    return bool(_LOOKS_LIKE_ID.match(v) or _LOOKS_LIKE_MONEY.match(v))
