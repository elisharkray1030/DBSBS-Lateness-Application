# Contributing

How to set up, run, and check the project as a developer. For what the app does
and how to deploy it on the client's Windows host, see [README.md](README.md).

## Before you start

Read these first so a change matches the existing design:

- [CONTEXT.md](CONTEXT.md) — the domain glossary: the words the project uses.
- [docs/architecture.md](docs/architecture.md) — the module map, the seams, and the invariants a change must not break.
- [docs/adr/](docs/adr/) — the decisions behind those boundaries.

## Development setup

- Install Python 3.11+ (CI tests 3.11 and 3.12; Docker uses 3.12-slim).
- Install the dependencies in the environment you will run from. `requirements-dev.txt` includes the runtime dependencies:

  ```bash
  python -m pip install -r requirements-dev.txt
  ```

  On Windows, if `python3` points at the Microsoft Store stub instead of a real interpreter, use `py -3 -m pip install -r requirements-dev.txt`.
- Set `SECRET_KEY` (required: the app aborts at startup without one). `flask run` does not read `.env` (python-dotenv is not installed), so set it directly. Generate a per-host secret, then export it:

  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

  ```bash
  # macOS / Linux
  export SECRET_KEY="<the generated secret>"
  ```

  ```powershell
  # Windows PowerShell
  $env:SECRET_KEY = "<the generated secret>"
  ```

- Prepare the database (first start only; a safe no-op afterwards):

  ```bash
  python -m flask --app app init-db
  ```

- Start the app for local development, then open `http://127.0.0.1:5000/`:

  ```bash
  python -m flask --app app run
  ```

## Checks

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs these on every push and pull request, so run them before opening a PR:

- **Tests:** `python -m pytest tests`. Covers the ingestion and storage seams, the Flask test-client routes, and the Playwright browser seam (synthetic CSVs and an in-memory SQLite connection). Browser tests need `python -m playwright install chromium` and skip automatically when it is unavailable.
- **Typecheck:** `python -m mypy` (configuration in `pyproject.toml`). CI pins the module list in `.github/workflows/ci.yml`.

## Dev tooling

- **Parser CLI:** `python parser.py`. It streams `namelist.csv` plus `test_data.csv` through the same ingestion module the web Import uses, writes `lateness_final_report.csv`, and prints diagnostics (rows read, matched rows, unmatched names, unparseable rows). Both files are local samples (`test_data.csv` is gitignored and not in the repo), so supply them first. The web route and the CLI share one ingestion path, so they cannot drift.
- **Demo data:** `python seed_demo_data.py` — deterministic months (January through August, excluding June). Optional flags: `python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]`. Under Docker, run the equivalent with `docker compose run --rm seed`.

## Conventions

- **Commits:** `type: subject (#issue)` — `feat:`, `fix:`, `refactor:`, `docs:`, and so on, matching the existing history.
- **Branches and PRs:** branch off `dev`, then open a pull request into `main`.
- **Invariants:** don't break the ones in [docs/architecture.md](docs/architecture.md).

## Issues and triage

- Issues and specs live as GitHub issues. See [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) and [docs/agents/triage-labels.md](docs/agents/triage-labels.md).
- [AGENTS.md](AGENTS.md) is the tooling entry point for agents working in this repo.
