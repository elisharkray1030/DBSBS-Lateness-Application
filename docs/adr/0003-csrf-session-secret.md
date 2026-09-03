# CSRF session secret and per-session tokens

Every state-changing request — Monthly Log Import building a Monthly Report,
Master List add/import/edit/Remove, Monthly Report deletion, Punishment
assignment and transition — carries a per-session token validated before any
stored data is touched. The session secret comes from the environment with no
built-in fallback: startup aborts with a clear message when it is unset or
empty. Session cookies are HttpOnly and SameSite=Lax with no Secure flag,
because the deployment is plain HTTP on the office LAN and browsers would
drop a Secure cookie over HTTP. The LAN itself is the trust boundary; the
Secure flag returns with HTTPS termination later. Tokens are hand-rolled
(random URL-safe value in the session, hidden field on forms, custom header
on fetch mutations) rather than a CSRF extension, keeping dependencies at
Flask plus gunicorn. Rejection leaves the Report Archive, Master List, and
Punishments untouched: page forms answer forbidden with a staff-visible
error, script endpoints answer forbidden with a structured error in the
existing shape.

## Considered Options

- **Session secret: hard-fail vs built-in fallback.** Rejected fallback —
  every deployment sharing one literal signing key lets one site forge
  another's session, and the failure is silent. Aborting startup forces each
  office PC and container to carry its own secret; tests carry an explicit
  test secret instead.
- **Secure flag: on vs off over plain HTTP.** Rejected on — a Secure cookie
  is never sent over the LAN's plain HTTP, so sessions (and their tokens)
  would silently break everywhere. Off is a deliberate, documented
  trust-boundary trade-off: the app is reachable only on the office LAN,
  never off-campus.
- **Token mechanism: hand-rolled vs Flask-WTF extension.** Rejected the
  extension — one more dependency for a single per-session comparison the
  app already owns (session store, form rendering, fetch calls). The
  hand-rolled check accepts the form field or the custom header on every
  mutation, so bulk Master List updates and single-Boarder edits share one
  gate.
