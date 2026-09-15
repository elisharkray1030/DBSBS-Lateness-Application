# One shared Discipline Audit across both disciplinary lifecycles

Punishments and I-Points are parallel lifecycles, but only I-Points retained
provenance: every I-Point write left an audit row, while a Punishment's
assignment and transitions recorded nothing beyond the current row state. The
two also mirrored vocabulary by hand — I-Points carried its own copy of the
`phone_held` token — so a rename in one lifecycle could silently break the
other.

We decided both lifecycles share one provenance record and one vocabulary home.
`ipoint_audit` is renamed to `discipline_audit` and gains a nullable `note`;
every Punishment assignment and transition now leaves an audit row, matching
I-Points; the shared status vocabulary moves to `records.py` so neither
lifecycle imports the other and the `phone_held` mirror dies. The rename, the
`note` column, and one synthetic `assigned` backfill event per pre-existing
Punishment land as a single atomic migration inside `create_schema`, following
the existing idempotent-migration pattern (`_migrate_normalized_name_keys`,
`_migrate_drop_ipoint_adjustments`).

This pairs with [ADR 0001](0001-punishment-tracking-model.md): audit rows record
change over time, while the lifecycle rows keep the frozen-snapshot discipline
ADR 0001 established. It explicitly does **not** restructure the ADR 0005/0007
ledger tables (`ipoint_entries`, `confiscations`): only the audit table is
renamed, and it remains the provenance sidecar those decisions rely on.

## Considered Options

- **One shared table vs a second `punishment_audit` table.** Rejected parallel
  tables — identical shapes would each need their own stage/read/backfill, and
  the combined Discipline Audit History would become a union at every call site,
  re-coupling the two lifecycles at the read seam the shared table exists to
  remove.
- **Rename in place vs create-copy-drop.** Rejected create-copy-drop — it churns
  audit row ids and rewrites the All-Time List's audit-only keys for no gain,
  where `ALTER TABLE ... RENAME` preserves both.
- **Move all status vocabulary to `records.py` vs only the shared tokens.**
  Chose all — the mirror that caused the coupling was `phone_held`, but the same
  leakage exists wherever `app.py` or a template hardcodes a status list;
  centralising the strings makes vocabulary drift impossible rather than merely
  caught at the one boundary we happened to notice.
- **Audit every write vs leave Punishments un-audited.** Chose audit — staff
  trust needs the same provenance story on both lifecycles, and the issue's
  "consistent provenance" win is otherwise half-delivered.

## Consequences

- The All-Time List derivation (`_ipoint_all_time_sources`) now reads
  `discipline_audit`; audit-only Match Keys survive the rename and keep such
  boarders discoverable.
- `_migrate_normalized_name_keys` rekeys `discipline_audit` in place of
  `ipoint_audit`.
- `stage_discipline_audit` / `list_discipline_audit`, the
  `DisciplineAudit`/`DisciplineAuditDraft`/`DisciplineAuditNote` types, and the
  `discipline_audit` table replace their `IPoint*` names; no aliases survive.
- The Boarder Profile gains a combined Discipline Audit History, newest-first,
  with one shared note type and a per-lifecycle describer for each lifecycle's
  own entity types.
- `is_due` is disambiguated by lifecycle: a Punishment's flag is
  `deadline_passed`, a Confiscation's is `due_for_release`.
