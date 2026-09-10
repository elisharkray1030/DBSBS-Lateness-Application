"""Production entry point for the designated office-LAN host.

Serves the Flask app under waitress (Windows-friendly WSGI; gunicorn is
Unix-only). Prepares the database first with the same idempotent ``init-db``
step the Docker image runs, then binds to all interfaces so staff PCs can
reach it. The office LAN is the trust boundary: plain HTTP, no auth.
"""

import os

from waitress import serve

from app import app, init_db


def main() -> None:
    """Prepare the store, then serve until stopped."""
    init_db(app)
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
