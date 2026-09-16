"""Generic page perception -- works on ANY page, not just ours.

Uses Playwright's built-in ARIA-snapshot engine, which computes real
accessible names the same way a browser exposes them to a screen reader.
No page-specific code lives here; this is a single, reusable function.
"""
from __future__ import annotations

from playwright.sync_api import Page


def perceive(page: Page) -> dict:
    """Returns a compact, model-friendly description of the current page:
    URL, title, the accessibility tree (role+name of everything visible),
    and the text of any alert/status region (where business outcomes and
    errors surface) called out separately since decisions hinge on it."""
    tree = page.locator("body").aria_snapshot()

    alerts = []
    for region in page.locator('[role="alert"], [role="status"]').all():
        try:
            text = region.inner_text(timeout=1000).strip()
            if text:
                alerts.append(text)
        except Exception:
            pass

    return {
        "url": page.url,
        "title": page.title(),
        "accessibility_tree": tree,
        "alerts": alerts,
    }
