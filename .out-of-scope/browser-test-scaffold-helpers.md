# Shared browser month-detail test helpers

The repeated four-line browser scaffold — seed the month, fetch the page, set
page content, open the month detail — used across the month-detail browser
tests stays inline at each call site. There is no shared helper that folds it
into a single call, by maintainer decision.

## Why this is out of scope

The fold was first deferred on #162 (don't consolidate before the #134 and
#131 rework lands), then rejected outright by the maintainer during the #166
close-out once that rework had landed. The reasoning that holds the decision:

- The scaffold is explicit: each test shows exactly how its page is built, so
  a failure reads locally. A shared helper would trade that locality for
  brevity across ~14 call sites without changing coverage or behaviour.
- The one real delivery concern behind the tests — getting the relocated
  `static/app.js` to execute in `set_content` pages — is already handled
  centrally by the `browser_page` fixture's inline shim (landed with #166).
  What remains duplicated is cosmetic setup, not mechanism.

No defect motivated the request and none is conceded by the rejection; this is
a standing preference for explicit test setup over shared-helper indirection
in this suite.

## Prior requests

- #163 — "Test helper: fold browser month-detail scaffold"
