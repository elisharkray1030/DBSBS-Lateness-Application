# UI Review Brief

Whole-UI review of this repo's frontend, produced by interview with the maintainer on 2026-08-21. This file is the **spec source** for a `/code-review` session: follow the code-review skill's process, with the overrides and additions below. Where this brief and the skill disagree, this brief wins.

## Baseline and scope

- **Fixed point**: the empty git tree (`4b825dc642cb6eb9a060e54bf8d69288fbee4904`), so `git diff <empty-tree>...HEAD` is the entire tree — a whole-UI audit, not a change review.
- **In scope**: `templates/index.html` (all HTML/CSS/JS — the UI is one 1567-line Jinja template), the routes in `app.py` that render or feed it (JSON endpoints included), and the UI-coupled tests in `tests/test_app.py`.
- **Out of scope**: auth/CSRF (deliberately parked in the spec's out-of-scope list), backend logic with no UI surface.

## Phase 0 — runtime evidence (before spawning axis agents)

Static reading alone is insufficient; every behavioral claim needs execution behind it.

1. Add `playwright` to `requirements-dev.txt`, then `pip install -r requirements-dev.txt` and `playwright install chromium`. **Done when**: both Playwright tests in `tests/test_app.py` (currently silently skipped) execute rather than skip.
2. Start the app against a throwaway database: set `DB_PATH` to a path outside the repo (or under gitignored `data/`), run `python -m flask --app app run`. The roster auto-seeds from root `namelist.csv` when the boarders table is empty. **Done when**: the Boarders tab lists the full roster. Never touch the real `lateness_history.db` at the repo root.
3. Copy `data/raw/Test Monthly Log (Month) .csv` to a sibling under `data/raw/` (gitignored) and zero-pad single-digit hours in the `Transaction Time` column — the parser regex rejects unpadded times, so unpadded input yields ~zero lateness. Import the padded copy through the Reports tab with a `YYYY-MM` label. **Done when**: a month card renders with non-zero lateness rows.
4. Click through for evidence: all four tabs, month-detail sorting, consequences status transitions, boarder inline edit (and cancel), destructive actions and their confirm modal, print preview, error/success banners on failed imports. Record what you observe; feed observations to all three axis agents.

## Axis 1 — Standards

Standards sources (this repo has no CODING_STANDARDS.md):

- `CONTEXT.md` — ubiquitous language; the **UI copy rules are hard violations** (tab reads "View Reports in Database"; "Import" not "upload/generate"; "Remove" not "delete" for boarders).
- `README.md` — behavior contract, notably: display-only sorting must not change print/download output.
- `docs/adr/0001-punishment-tracking-model.md` — manual-only punishment state machine and computed display flags; UI must not imply automated state changes.

Plus the code-review skill's smell baseline as usual. Scope: the three file groups above.

## Axis 2 — Spec

Per-surface spec mapping:

| Surface | Spec source |
|---|---|
| Consequences tab | `docs/specs/punishment-tracking.md` (including its UI section and out-of-scope list) |
| Reports / Search History / Boarders tabs, shared chrome | `CONTEXT.md` copy rules + README "Using the application" walkthrough, treated as de-facto spec |

Items in the written spec's out-of-scope list are deferred by decision — flagging them belongs to Axis 3, not here. Quote the governing spec line for each finding.

## Axis 3 — Gaps (third parallel agent)

Five lenses; a gap is missing or broken *behavior*, not style:

1. **Journey completeness** — dead ends and missing links (e.g. search-history results offer no path to the underlying month).
2. **State coverage** — loading, error, and empty states for every async action.
3. **Accessibility** — manual heuristics only: keyboard-only walkthrough, focus visibility, label association, contrast spot-checks. No axe-core, no new dependencies.
4. **Print/responsive** — print output correctness and narrow-viewport behavior.
5. **Destructive-action safety** — confirm flows, undo absence, ambiguity of irreversible verbs.

Findings that are genuinely new ideas rather than defects (pagination, bulk actions, history→month links) are tagged **backlog candidate** and stay report-only — they are never ticketized without explicit maintainer promotion.

## Structural debt (separate category, non-blocking)

Flag structural findings in their own list, never mixed into the axes: monolithic template (inline `<style>`/`<script>`), mixed `onclick=` attributes vs `addEventListener`, the dark-mode token TODO, and test coupling that asserts raw JS strings inside the template (refactoring will break tests). Report cost and a suggested decomposition order; do not treat as failures.

## Report format

One merged report:

- Sections `## Standards`, `## Spec`, `## Gaps`, `## Structural debt` — axes stay separate; no cross-axis reranking.
- Every finding: ID (`S1`, `P1`, `G1`, `D1`…), `file:line`, tier (**P0** breaks a core task or loses/corrupts data · **P1** degrades a task · **P2** polish), effort (S/M/L), and evidence (quote, screenshot description, or observed behavior from Phase 0).
- Each axis section ends with a one-line verdict: clean, or worst finding.
- Final summary: counts per tier per axis, and the P0 list if any.

## Follow-through (only on maintainer acceptance)

Present the report and stop. After the maintainer names accepted finding IDs:

- One GitHub issue per finding via `gh` per `docs/agents/issue-tracker.md`; trivial nits grouped into a single cleanup issue.
- Labels per `docs/agents/triage-labels.md`: clear violations of documented rules → `ready-for-agent`; judgment calls and gap hunches → `needs-triage`.
- Backlog candidates are never ticketized in this flow.

## Hard guardrails

- Review only: zero code fixes. The sole permitted file edit is adding `playwright` to `requirements-dev.txt`, left **uncommitted**.
- Zero commits, zero pushes, for any reason.
- All runtime data lives in the throwaway `DB_PATH` database and gitignored `data/raw/` copies.
