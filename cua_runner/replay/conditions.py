"""Evaluates a schema Condition against live page state. Used for
checkpoints, outcome detection, and step-level asserts -- one evaluator,
shared by all three, so "did we reach the expected state" always means
the same thing everywhere in the engine.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from playwright.sync_api import Page

from cua_runner import schema as sc
from cua_runner.replay.locator_resolver import resolve


@dataclass
class ReplayContext:
    """Tracks state that isn't directly queryable from the page object,
    e.g. the last HTTP status code of the main document response."""
    page: Page
    last_status_code: int = 200


def evaluate(condition: sc.Condition, ctx: ReplayContext) -> bool:
    if condition.kind == sc.ConditionKind.URL_MATCH:
        path = "/" + ctx.page.url.split("://", 1)[-1].split("/", 1)[-1] if "/" in ctx.page.url.split("://", 1)[-1] else "/"
        return fnmatch.fnmatch(path, condition.url_pattern)

    if condition.kind == sc.ConditionKind.ELEMENT_PRESENT:
        try:
            resolve(ctx.page, condition.locator)
            return True
        except Exception:
            return False

    if condition.kind == sc.ConditionKind.TEXT_IN_REGION:
        try:
            region_locator = ctx.page.get_by_role(
                condition.region.role,
                name=condition.region.name if condition.region.name else None,
                exact=True,
            ) if condition.region.name else ctx.page.get_by_role(condition.region.role)
            count = region_locator.count()
            for i in range(count):
                text = region_locator.nth(i).inner_text(timeout=2000)
                if condition.text_pattern.lower() in text.lower():
                    return True
            return False
        except Exception:
            return False

    if condition.kind == sc.ConditionKind.STATUS_CODE:
        return ctx.last_status_code == condition.status_code

    if condition.kind == sc.ConditionKind.ALL_OF:
        return all(evaluate(c, ctx) for c in condition.all_of)

    return False
