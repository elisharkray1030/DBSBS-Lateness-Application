# Spec — CSRF protection for the Lateness Dashboard

Status: ready-for-agent

## Problem Statement

The app lifts the parked "Trust" item from `docs/specs/punishment-tracking.md:75`. The deployment is now a shared, office-LAN multi-writer setup: every colleague runs the app on their own PC against one SQLite database on the NAS. POST routes accept form data and mutate the database (`/`, `/assign/...`, `/punishment/.../transition`, boarder CRUD) with no CSRF token, and the session secret falls back to a hard-coded default. Because staff now use real browsers against a shared server-less deployment on the office network, cross-site request forgery is a live concern: a staff member browsing the web on the same machine could be tricked into submitting a mutation form.

## Solution

Add CSRF protection to every state-changing form, require a real session secret, and lock down session cookies to the office-LAN trust boundary.

- **Session secret from environment, hard-fail if unset.** `app.py:54` currently falls back to the literal `"dbs-lateness-dashboard-local"`. Replace with: read `SECRET_KEY` from the environment; refuse to start (clear `SystemExit` message) when unset. The Docker/compose path must inject it too.
- **CSRF token on every state-changing form.** All POST forms (`/`, `/assign/...`, `/punishment/.../transition`, boarder add/import, and the fetch-based PATCH/DELETE calls) include a per-session token. Server-side, each mutation route rejects requests whose token does not match the session token with a clear error (flash + 4xx), leaving the database untouched. Choose the mechanism: Flask-WTF's CSRF extension, or a small hand-rolled token (session-stored `secrets.token_urlsafe`, hidden input, and a `before_request`/decorator check for non-GET requests). Prefer the hand-rolled approach if it keeps dependencies down; the app currently has no extension beyond Flask.
- **Session cookie flags.** `HttpOnly` + `SameSite=Lax` on the session cookie. **No `Secure` flag** — the deployment is plain-HTTP on the office LAN, and a `Secure` cookie would be dropped by browsers over HTTP. Document that this is a deliberate, trust-boundary trade-off: the app is only reachable on the office LAN, never off-campus.
- **JSON/fetch mutation endpoints.** The `PATCH`/`DELETE` endpoints (`/api/boarders/...`, `/delete_month/...`) are called via `fetch`. They must carry the token too (custom header, e.g. `X-CSRF-Token`, read from a meta tag or the form). Include this in the acceptance criteria; it is the part most commonly missed.

## Out of Scope

- Authentication / login (still no auth; the office LAN is the trust boundary — see `docs/specs/punishment-tracking.md:75`).
- HTTPS termination or TLS certs (would allow the `Secure` flag later; not in this iteration).
- Audit log (parked, per the original spec).

## Testing Decisions

- Test via the Flask `test_client` (prior art `tests/test_app.py`): a POST without a token is rejected and the database is unchanged; a POST with the correct token succeeds.
- Because the token is session-bound, the test client must first GET a page to populate the session, then submit the matching token.
- The `fetch`-based endpoints are exercised the same way with the custom header.
- Verify the app refuses to start when `SECRET_KEY` is unset (subprocess or monkeypatched `os.environ` check).