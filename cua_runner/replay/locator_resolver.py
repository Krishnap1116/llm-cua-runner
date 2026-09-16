"""Resolves a schema Locator (primary + fallback chain) against a live
page. Tries primary first; on failure, tries each fallback in order.
Logs which strategy actually succeeded -- a fallback firing instead of
primary is itself an early UI-drift signal worth recording, even when the
step otherwise succeeds (Section 3.7).
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from cua_runner import schema as sc
from cua_runner.agent.tools import value_locator_for


class LocatorResolutionError(Exception):
    pass


@dataclass
class ResolvedLocator:
    locator: object  # a Playwright Locator
    strategy_used: sc.LocatorStrategy
    used_fallback: bool


def _build_locator(page: Page, strategy: sc.LocatorStrategy):
    if strategy.strategy == sc.LocatorStrategyKind.ROLE_NAME:
        return page.get_by_role(strategy.role, name=strategy.name, exact=True)
    if strategy.strategy == sc.LocatorStrategyKind.LABEL_TEXT:
        return page.get_by_label(strategy.name, exact=True)
    if strategy.strategy == sc.LocatorStrategyKind.TEXT:
        return page.get_by_text(strategy.name, exact=True)
    if strategy.strategy == sc.LocatorStrategyKind.CSS_STRUCTURAL:
        return page.locator(strategy.css)
    raise LocatorResolutionError(f"unknown locator strategy: {strategy.strategy}")


def resolve(page: Page, locator: sc.Locator, *, for_value: bool = False, timeout_ms: int = 3000) -> ResolvedLocator:
    """for_value=True applies the label->sibling-value rule for extract
    steps targeting a legacy table cell -- the wait/existence check
    happens on the LABEL locator itself, since the value locator's
    existence is meaningless before the label resolves.

    Waits up to timeout_ms per candidate (real render/network delay,
    not an instant check) before falling back to the next strategy."""
    candidates = [locator.primary] + list(locator.fallback)
    for i, strategy in enumerate(candidates):
        try:
            base = _build_locator(page, strategy)
            base.first.wait_for(state="attached", timeout=timeout_ms)
            resolved = value_locator_for(base, strategy.role) if for_value else base
            return ResolvedLocator(locator=resolved, strategy_used=strategy, used_fallback=(i > 0))
        except Exception:
            continue
    raise LocatorResolutionError(
        f"could not resolve any locator strategy (tried {len(candidates)}): "
        f"primary={locator.primary.model_dump()}"
    )
