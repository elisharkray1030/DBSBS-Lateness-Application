# Retain inline `onchange` on the filter selects

The UI-refresh work (#241) migrates inline event-handler attributes to
`addEventListener`, but the filter `<select onchange="this.form.submit()">`
controls (two on the home Punishments panel, two on the dashboard, one on the
I-Points Confiscation panel) keep their inline handler deliberately.

These controls are the app's progressive-enhancement seam. The inline handler is
the only submit path that survives **both** JavaScript being disabled (where the
adjacent `<noscript>` Filter button takes over) and `static/app.js` failing to
load (a 404, a blocked request, or a thrown error). Registering the handler in
`app.js` would make filtering depend on that script, reintroducing the class of
regression #244 fixed for the I-Points Entry Save button: a server-rendered
control stranded because the script that was supposed to wire it never ran.
Keeping the handler on the element removes the script from the critical path
entirely.

This is an explicit, bounded deviation from "all handlers live in scripts", not
an oversight: a future reader should not "fix" it.

## Considered Options

- **Keep the inline `onchange` on the filter selects.** Chosen — robust to both
  JavaScript-disabled and `app.js`-failure, at the cost of one inconsistent
  wiring style.
- **Register the filters in `static/app.js`.** Rejected — filtering would break
  whenever `app.js` fails, the exact regression #244 addressed.
- **A small standalone filter script loaded before the page scripts.** Rejected —
  it still adds a script to the critical path without the inline handler's
  guarantee against a failed/missing asset.
- **Plain form submit with no JS at all.** Rejected — loses the immediate
  "change selects, form submits" behaviour staff rely on when JavaScript is
  available.

## Consequences

- The filter selects intentionally carry inline `onchange`; all other inline
  handlers are migrated to `addEventListener` (home report view/sort/print/close
  in `static/app.js`; the confirm modal Yes/Cancel in `static/layout.js`).
- Any future change to a filter's submit behaviour must not assume `app.js` has
  run. The `<noscript>` Filter button remains the no-JS path.
- A lint rule that bans inline handlers would produce a false positive here.
