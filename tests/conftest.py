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
def fresh_client(tmp_path):
    """A Flask test client over a throwaway database seeded with ALICE/BOB.

    Built through the application factory with inline config, so no
    environment-before-import setup is needed. The application context
    stays pushed for the test, so ``app_module.connect()`` in the test body
    resolves to this fixture's database.
    """
    import app as app_module

    db_path = tmp_path / "test.db"
    namelist = tmp_path / "namelist.csv"
    namelist.write_text(
        "Bed,Name\n601A,ALICE\n601B,BOB\n",
        encoding="utf-8",
    )
    app = app_module.create_app(
        {
            "DB_PATH": str(db_path),
            "NAMELIST_PATH": str(namelist),
            "TESTING": True,
        }
    )
    pushed = app.app_context()
    pushed.push()
    app_module.init_db()
    yield app.test_client()
    pushed.pop()


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
