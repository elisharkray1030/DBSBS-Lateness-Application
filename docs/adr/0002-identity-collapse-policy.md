# Identity collapse: one identity per Match Key

Name matching runs through the punctuation-insensitive Match Key (uppercased,
every run of non-alphanumerics collapsed to a single space), so Master List
spellings like "SURNAME, Given" match log rows like "SURNAME Given". When two
stored spellings collapse onto the same key, they are treated as one identity
— the same semantics ingress dedup already applies when an imported roster
holds duplicate spellings. The startup migration that re-keys legacy rows onto
the current Match Key resolves collisions deterministically: rows are
processed in ascending id order, the first row to claim a new key wins, and
the loser keeps its legacy key rather than blocking startup. Kept keys are
counted in the meta table and surfaced once per session as a home-tab banner;
resolving the ambiguity is staff data-entry work, so automation stops at
detection. Re-keying touches only the normalized_name column — frozen
punishment fields (points, bed, display name) are never mutated, per
ADR 0001.

## Considered Options

- **Collision handling: auto-merge vs detection only.** Rejected auto-merge
  (rewriting the loser's Boarder History and Punishments onto the winner) —
  two spellings can be two real people or one person entered twice, and
  deciding which is staff judgment; silently merging risks misattributing
  lateness and disciplinary consequences. The banner makes the ambiguity
  visible instead.
- **Migration collision: first-row-wins vs fail startup.** Rejected
  fail-startup — a legacy database holding two spellings of one name would
  refuse to open with no path forward for staff. Keeping the loser on its
  legacy key always opens the app, loses nothing, and tells staff exactly
  which records need manual care.
- **Frozen fields during re-key:** re-keying updates normalized_name only;
  per ADR 0001, punishment points, bed, and display_name stay exactly as
  assigned, so a corrected name never rewrites an issued Punishment.
