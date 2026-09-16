"""Generic, app-agnostic authentication for the replay engine.

Login is never part of a recorded capability's steps (see agent/loop.py
and recorder.py) -- it's handled here, once, before any capability steps
run, using credentials the replay engine itself holds (a service account),
never anything from the artifact. Detection uses the standard HTML
`autocomplete` tokens (username/current-password), the same generic
mechanism used to exclude these fields during recording, so this works on
any login form built the normal way -- not just this mock app.
"""
from __future__ import annotations

import os

from playwright.sync_api import Page


class AuthenticationError(Exception):
    pass


def ensure_authenticated(page: Page, base_url: str) -> None:
    username = os.environ.get("SERVICE_ACCOUNT_USERNAME")
    password = os.environ.get("SERVICE_ACCOUNT_PASSWORD")
    if not username or not password:
        raise AuthenticationError(
            "SERVICE_ACCOUNT_USERNAME/SERVICE_ACCOUNT_PASSWORD are not configured -- "
            "the replay engine cannot authenticate without its own held credentials."
        )

    page.goto(base_url, wait_until="load")
    if "/login" not in page.url:
        return  # already authenticated

    username_field = page.locator('[autocomplete="username"]').first
    password_field = page.locator('[autocomplete="current-password"]').first
    if username_field.count() == 0 or password_field.count() == 0:
        raise AuthenticationError("Could not find a standard login form on the target app.")

    username_field.fill(username, timeout=5000)
    password_field.fill(password, timeout=5000)
    password_field.press("Enter")
    page.wait_for_load_state("load", timeout=5000)

    if "/login" in page.url:
        raise AuthenticationError("Login failed with the configured service-account credentials.")
