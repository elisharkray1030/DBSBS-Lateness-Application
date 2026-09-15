import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

import storage

# Test runs provide an explicit session secret so module-level ``import app``
# (which hard-fails without one in production) stays importable. Production
# still refuses to boot without SECRET_KEY; every factory call below also
# passes it inline.
os.environ.setdefault("SECRET_KEY", "test-secret-key")

# Keep Monthly Log archives out of the repository worktree: ad-hoc
# application factories that exercise an Import would otherwise write to the
# default ``data/logs`` under the current directory. Assigned (not
# setdefault) so a developer's real LOG_ARCHIVE_DIR can never leak into tests.
os.environ["LOG_ARCHIVE_DIR"] = tempfile.mkdtemp(prefix="lateness-archive-")


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
            "SECRET_KEY": "test-secret-key",
            "TESTING": True,
        }
    )
    pushed = app.app_context()
    pushed.push()
    app_module.init_db()
    yield app.test_client()
    pushed.pop()


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_APP_JS = _STATIC_DIR / "app.js"
_APP_JS_TAG = '<script src="/static/app.js"></script>'
_APP_CSS = _STATIC_DIR / "app.css"
_APP_CSS_TAG = '<link rel="stylesheet" href="/static/app.css">'


def _inline_static_assets(html):
    """Inline local static assets into set_content HTML (#166, umbrella #131).

    Browser tests feed Flask-rendered HTML to Chromium via
    ``page.set_content``, whose document URL (about:blank) cannot resolve a
    relative ``<script src>`` or ``<link href>`` — the browser never even
    issues the request, so ``page.route`` cannot help. Inlining the
    byte-identical files restores the exact pre-extraction execution
    environment with zero per-test edits. Retire this only when the suite
    stops feeding rendered HTML to ``set_content`` — the #163 test-helper fold
    keeps ``set_content``, so that ticket does not retire this shim.
    """
    if _APP_CSS_TAG in html:
        css = _APP_CSS.read_bytes().decode("utf-8")
        assert "</style" not in css.lower(), "app.css is no longer safe to inline"
        html = html.replace(_APP_CSS_TAG, "<style>" + css + "</style>")
    if _APP_JS_TAG in html:
        js = _APP_JS.read_bytes().decode("utf-8")
        assert "</script" not in js.lower(), "app.js is no longer safe to inline"
        html = html.replace(_APP_JS_TAG, "<script>" + js + "</script>")
    return html


@pytest.fixture
def browser():
    """Yields a headless Chromium browser; skips when Playwright is unavailable."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def browser_page(browser):
    """Yields a headless Chromium page with local static assets inlined into
    set_content (the stylesheet and app.js)."""
    page = browser.new_page()
    set_content = page.set_content

    def set_content_with_static(html, **kwargs):
        return set_content(_inline_static_assets(html), **kwargs)

    page.set_content = set_content_with_static
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def js_page(browser):
    """A JavaScript-enabled page that does not inline app.js.

    Use when a test must control whether app.js runs (for example, blocking
    the request to simulate a failed script).
    """
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def no_js_page(browser):
    """A page with JavaScript disabled, for <noscript> coverage."""
    context = browser.new_context(java_script_enabled=False)
    try:
        yield context.new_page()
    finally:
        context.close()
