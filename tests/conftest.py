import sqlite3

import pytest

import storage


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    storage.create_schema(connection)
    yield connection
    connection.close()


@pytest.fixture()
def fresh_client(tmp_path, monkeypatch):
    """A Flask test client over a throwaway database seeded with ALICE/BOB."""
    import app as app_module

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
    namelist = tmp_path / "namelist.csv"
    namelist.write_text(
        "Bed,Name\n601A,ALICE\n601B,BOB\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
    app_module.init_db()
    return app_module.app.test_client()


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
