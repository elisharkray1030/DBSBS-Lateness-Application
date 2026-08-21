import sqlite3

import pytest

import storage


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    storage.create_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def browser_page():
    """Yields a headless Chromium page; skips when Playwright is unavailable."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()
