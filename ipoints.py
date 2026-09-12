"""I-Point ledger lifecycle: logging Entries, derived Balance, and audit.

Stands beside ``punishments.py`` as the second disciplinary lifecycle. The
route layer stays a thin adapter: this module validates a submission, writes
the ledger and its audit row in one transaction, and derives each boarder's
Balance from the ledger rather than storing it.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from records import (
    BoarderIdentity,
    Confiscation,
    IPointAdjustment,
    IPointAudit,
    IPointAuditDraft,
    IPointAuditNote,
    IPointEntry,
    IPointSummary,
    bed_sort_key,
)

import storage

_OVERDRAW = "That change would take the Boarder's I-Point Balance below zero."

# The month-close tiers, smallest first: the largest at or below the Balance
# becomes the pending Redemption, capped at the largest tier.
TIERS = (5, 10, 15)

# The phone period each tier buys, as (unit, count): days or calendar months.
TIER_PERIODS = {
    5: ("day", 1),
    10: ("day", 7),
    15: ("month", 1),
}

# The fixed phone period each tier buys, shared by the UI and the release date.
TIER_PERIOD_LABELS = {
    5: "1 day",
    10: "1 week",
    15: "1 calendar month",
}

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_RELEASED = "released"
STATUS_VOIDED = "voided"

# Only confirmed Redemptions debit the Balance; a pending row reserves points
# but does not subtract them (ADR 0007).
_CONFIRMED_STATUSES = (STATUS_ACTIVE, STATUS_RELEASED)
# A pending or active Confiscation is "open": it blocks a second Redemption.
_OPEN_STATUSES = (STATUS_PENDING, STATUS_ACTIVE)

# A lateness Punishment in this status makes an active Confiscation "Stacked":
# the phone is held for both reasons (CONTEXT.md, "Stacked"). Mirrors the
# `phone_held` vocabulary owned by punishments.py without importing it, keeping
# the two lifecycles parallel.
_PHONE_HELD_PUNISHMENT = "phone_held"


def points_phrase(points: int) -> str:
    """Words a point count with its singular/plural I-Point noun."""
    noun = "I-Point" if points == 1 else "I-Points"
    return f"{points} {noun}"


@dataclass
class EntrySaved:
    """An I-Point Entry was logged."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        return (
            f"Logged {points_phrase(self.points)} for {self.display_name} "
            f"on {self.occurred_on}."
        )


@dataclass
class ChangeRejected:
    """An I-Point ledger write was refused; ``reason`` is user-facing."""

    reason: str


@dataclass
class EntryRejected(ChangeRejected):
    """The submitted I-Point Entry is not valid."""


@dataclass
class AdjustmentSaved:
    """An I-Point Adjustment was added."""

    normalized_name: str
    display_name: str
    points: int
    reason: str

    @property
    def message(self) -> str:
        return (
            f"Added an adjustment of {points_phrase(self.points)} "
            f"for {self.display_name}."
        )


@dataclass
class AdjustmentEdited:
    """An I-Point Adjustment's points or reason was corrected."""

    normalized_name: str
    display_name: str
    points: int
    reason: str

    @property
    def message(self) -> str:
        return (
            f"Updated {self.display_name}'s adjustment to "
            f"{points_phrase(self.points)}."
        )


@dataclass
class AdjustmentRemoved:
    """An I-Point Adjustment was removed; its prior state stays in the audit."""

    normalized_name: str
    display_name: str
    points: int
    reason: str

    @property
    def message(self) -> str:
        return (
            f"Removed {self.display_name}'s adjustment of "
            f"{points_phrase(self.points)}."
        )


@dataclass
class AdjustmentRejected(ChangeRejected):
    """The submitted I-Point Adjustment is not valid."""


@dataclass
class RedemptionConfirmed:
    """A pending Redemption was confirmed into an active Confiscation."""

    normalized_name: str
    display_name: str
    points_redeemed: int
    release_due: str

    @property
    def message(self) -> str:
        return (
            f"Confirmed the redemption of {points_phrase(self.points_redeemed)} "
            f"for {self.display_name}; phone due back {self.release_due}."
        )


@dataclass
class ConfiscationVoided:
    """A pending Redemption or active Confiscation was voided."""

    normalized_name: str
    display_name: str
    points_redeemed: int
    was_pending: bool = False

    @property
    def message(self) -> str:
        subject = "pending redemption" if self.was_pending else "Confiscation"
        return (
            f"Voided the {subject} of "
            f"{points_phrase(self.points_redeemed)} for {self.display_name}."
        )


@dataclass
class ConfiscationReleased:
    """An active Confiscation was marked released."""

    normalized_name: str
    display_name: str
    points_redeemed: int
    released_at: str

    @property
    def message(self) -> str:
        return (
            f"Marked the Confiscation of {points_phrase(self.points_redeemed)} "
            f"for {self.display_name} released."
        )


@dataclass
class ConfiscationEdited:
    """An active Confiscation's tier, and so its period, was corrected."""

    normalized_name: str
    display_name: str
    tier: int
    release_due: str

    @property
    def message(self) -> str:
        return (
            f"Updated {self.display_name}'s Confiscation to tier {self.tier} "
            f"(phone due back {self.release_due})."
        )


@dataclass
class ConfiscationRemoved:
    """A Confiscation was removed; its prior state stays in the audit."""

    normalized_name: str
    display_name: str
    points_redeemed: int

    @property
    def message(self) -> str:
        return (
            f"Removed {self.display_name}'s Confiscation of "
            f"{points_phrase(self.points_redeemed)}."
        )


@dataclass
class RedemptionRejected(ChangeRejected):
    """The pending Redemption could not be confirmed or voided."""


@dataclass
class ConfiscationRejected(ChangeRejected):
    """A Confiscation lifecycle change could not be applied."""


@dataclass(frozen=True)
class PendingRedemption:
    """One Boarder's month-close pending Redemption, before it is stored.

    The pure evaluator's output: which month triggered it, the locked tier, and
    the points the tier reserves. Persisting it makes it a
    :class:`records.Confiscation` with ``status='pending'``.
    """

    trigger_month: str
    tier: int
    points_redeemed: int


@dataclass
class EntryEdited:
    """An I-Point Entry's points, date, or reason was corrected."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        return (
            f"Updated {self.display_name}: {points_phrase(self.points)} "
            f"on {self.occurred_on}."
        )


@dataclass
class EntryRemoved:
    """An I-Point Entry was removed; its prior state stays in the audit."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        return (
            f"Removed {points_phrase(self.points)} for {self.display_name} "
            f"({self.occurred_on})."
        )


def today_iso() -> str:
    """Returns today's local calendar date as an ISO string."""
    return datetime.now().astimezone().date().isoformat()


def resolve_display_name(conn, normalized_name: str) -> str:
    """Resolves a Match Key to its freshest display name, or the key itself."""
    identity = storage.resolve_boarder_identity(conn, normalized_name)
    return identity.display_name if identity is not None else normalized_name


def _whole_int(text: str, *, signed: bool) -> "int | None":
    """Parses a stripped integer string, optionally admitting a leading sign.

    ``str.isdecimal`` (not ``str.isdigit``) gates the digits, so digit-class
    characters such as a superscript ``²`` — which pass ``isdigit`` but have no
    ``int`` value — are refused.
    """
    body = text[1:] if signed and text[:1] in "+-" else text
    if not body.isdecimal():
        return None
    return int(text)


def _coerce_points(points) -> "int | None":
    """Returns an integer candidate, or None for a non-whole-number value.

    Only integers and decimal-digit strings qualify, so a fractional value can
    never be silently truncated into an Entry.
    """
    if isinstance(points, bool):
        return None
    if isinstance(points, int):
        return points
    if isinstance(points, str):
        return _whole_int(points.strip(), signed=False)
    return None


def _coerce_signed_points(points) -> "int | None":
    """Returns a signed integer candidate, or None for a non-whole-number value.

    Like ``_coerce_points`` but admitting a leading sign, since an Adjustment
    may subtract.
    """
    if isinstance(points, bool):
        return None
    if isinstance(points, int):
        return points
    if isinstance(points, str):
        return _whole_int(points.strip(), signed=True)
    return None


def _parse_occurred_on(occurred_on: str | None) -> "str | None":
    """Returns the normalized ISO date, or None when blank or unparseable."""
    raw = (occurred_on or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _resolve_occurred_on(occurred_on: str | None, today: str | None) -> "str | None":
    raw = (occurred_on or "").strip()
    if not raw:
        return today or today_iso()
    return _parse_occurred_on(raw)


def _entry_fields(points: int, occurred_on: str, reason: str) -> dict[str, object]:
    """The auditable snapshot of an Entry's user-editable fields."""
    return {"points": points, "occurred_on": occurred_on, "reason": reason}


def _entry_state(entry: IPointEntry) -> dict[str, object]:
    """The auditable snapshot of one stored Entry's editable fields."""
    return _entry_fields(entry.points, entry.occurred_on, entry.reason)


def _adjustment_fields(points: int, reason: str) -> dict[str, object]:
    """The auditable snapshot of an Adjustment's user-editable fields."""
    return {"points": points, "reason": reason}


def _adjustment_state(adjustment: IPointAdjustment) -> dict[str, object]:
    """The auditable snapshot of one stored Adjustment's editable fields."""
    return _adjustment_fields(adjustment.points, adjustment.reason)


def _confiscation_fields(
    trigger_month: str,
    points_redeemed: int,
    tier: int,
    status: str,
) -> dict[str, object]:
    """The auditable snapshot of a newly created Confiscation."""
    return {
        "trigger_month": trigger_month,
        "points_redeemed": points_redeemed,
        "tier": tier,
        "status": status,
    }


def _confiscation_state(confiscation: Confiscation) -> dict[str, object]:
    """The auditable snapshot of one stored Confiscation."""
    return {
        "trigger_month": confiscation.trigger_month,
        "points_redeemed": confiscation.points_redeemed,
        "tier": confiscation.tier,
        "status": confiscation.status,
        "display_name": confiscation.display_name,
        "bed": confiscation.bed,
        "confirmed_at": confiscation.confirmed_at,
        "release_due": confiscation.release_due,
        "released_at": confiscation.released_at,
        "voided_at": confiscation.voided_at,
        "void_reason": confiscation.void_reason,
    }


def _dump_state(state: dict[str, object] | None) -> str | None:
    """Encodes one auditable snapshot as sort-keyed JSON, or None when absent."""
    return json.dumps(state, sort_keys=True) if state is not None else None


def _audit_draft(
    entity_type: str,
    entity_id: int,
    normalized_name: str,
    action: str,
    before_state: dict[str, object] | None,
    after_state: dict[str, object] | None,
    changed_at: str,
) -> IPointAuditDraft:
    """Builds one Audit draft, encoding both snapshots as JSON."""
    return IPointAuditDraft(
        entity_type=entity_type,
        entity_id=entity_id,
        normalized_name=normalized_name,
        action=action,
        before_state=_dump_state(before_state),
        after_state=_dump_state(after_state),
        changed_at=changed_at,
    )


def _net_balance(
    entries: list[IPointEntry],
    adjustments: list[IPointAdjustment],
    confiscations: list[Confiscation],
) -> int:
    """Sums a ledger slice: Entries plus Adjustments minus confirmed redemptions."""
    return (
        sum(entry.points for entry in entries)
        + sum(adjustment.points for adjustment in adjustments)
        - sum(
            confiscation.points_redeemed
            for confiscation in confiscations
            if confiscation.status in _CONFIRMED_STATUSES
        )
    )


def _balance_for(conn, normalized_name: str) -> int:
    """Derives one boarder's current I-Point Balance from the ledger."""
    return _net_balance(
        storage.list_ipoint_entries(conn, normalized_name),
        storage.list_ipoint_adjustments(conn, normalized_name),
        storage.list_ipoint_confiscations(conn, normalized_name),
    )


def _as_date(value: str) -> date:
    """Returns the local calendar date of an ISO timestamp or plain date.

    Stored recording timestamps are UTC; evaluating them in the machine's
    local zone matches the local calendar that ``today_iso`` and month-close
    evaluation use, so a row logged just after local midnight is not counted
    into the month that already closed.
    """
    try:
        return datetime.fromisoformat(value).astimezone().date()
    except ValueError:
        return date.fromisoformat(value)


def _last_closed_month_end(today: date) -> date:
    """Returns the last day of the latest fully-elapsed local calendar month."""
    return today.replace(day=1) - timedelta(days=1)


def _add_one_month(moment: date) -> date:
    """Returns ``moment`` advanced one calendar month, clamping the day."""
    year = moment.year + (1 if moment.month == 12 else 0)
    month = 1 if moment.month == 12 else moment.month + 1
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = (following - timedelta(days=1)).day
    return date(year, month, min(moment.day, last_day))


def release_due_for(tier: int, confirmed_on: date) -> date:
    """Returns the Confiscation's fixed release date for a tier.

    A tier of 5 is one day, 10 is one week, and 15 is one calendar month.
    """
    try:
        unit, count = TIER_PERIODS[tier]
    except KeyError:
        raise ValueError(f"Unknown Confiscation tier: {tier!r}") from None
    if unit == "day":
        return confirmed_on + timedelta(days=count)
    return _add_one_month(confirmed_on)


def _tier_for(balance: int) -> "int | None":
    """Returns the largest tier at or below the Balance, or None below 5."""
    eligible = [tier for tier in TIERS if tier <= balance]
    return max(eligible) if eligible else None


def _balance_as_of(
    entries: list[IPointEntry],
    adjustments: list[IPointAdjustment],
    confiscations: list[Confiscation],
    as_of: date,
) -> int:
    """Derives a boarder's Balance as it stood at the end of a closed month.

    Rows count by when they entered the ledger (their recording timestamps),
    never by an Entry's incident date, so a back-dated Entry added after a
    month closed counts only toward the next month's close and cannot reopen
    the closed one.
    """
    return _net_balance(
        [entry for entry in entries if _as_date(entry.recorded_at) <= as_of],
        [
            adjustment
            for adjustment in adjustments
            if _as_date(adjustment.recorded_at) <= as_of
        ],
        [
            confiscation
            for confiscation in confiscations
            if confiscation.confirmed_at is not None
            and _as_date(confiscation.confirmed_at) <= as_of
        ],
    )


def evaluate_month_close(
    entries: list[IPointEntry],
    adjustments: list[IPointAdjustment],
    confiscations: list[Confiscation],
    today: str,
    recorded_months: "frozenset[str]" = frozenset(),
) -> "PendingRedemption | None":
    """Pure month-close evaluation for one boarder, with ``today`` injected.

    Inspects the latest fully-elapsed local calendar month. When the Balance as
    of that month's end is at least 5, and no open Redemption exists and the
    month has never had one recorded, returns the largest tier at or below the
    Balance (capped at 15), locked at creation. Otherwise returns None.

    ``recorded_months`` carries the trigger months this Boarder has ever had a
    Redemption created for, read from the audit ledger so a removed
    Confiscation's month cannot be re-created (the live row is gone).
    """
    month_end = _last_closed_month_end(_as_date(today))
    trigger_month = month_end.strftime("%Y-%m")

    if any(
        confiscation.status in _OPEN_STATUSES
        for confiscation in confiscations
    ):
        return None
    if trigger_month in recorded_months or any(
        confiscation.trigger_month == trigger_month
        for confiscation in confiscations
    ):
        return None

    tier = _tier_for(_balance_as_of(entries, adjustments, confiscations, month_end))
    if tier is None:
        return None
    return PendingRedemption(
        trigger_month=trigger_month, tier=tier, points_redeemed=tier
    )


@dataclass
class _BoarderLedger:
    """One boarder's grouped ledger rows for month-close evaluation."""

    entries: list[IPointEntry] = field(default_factory=list)
    adjustments: list[IPointAdjustment] = field(default_factory=list)
    confiscations: list[Confiscation] = field(default_factory=list)


def _ledger_by_boarder(conn) -> dict[str, _BoarderLedger]:
    """Groups every ledger row by Match Key for month-close evaluation."""
    grouped: dict[str, _BoarderLedger] = {}

    def ledger_for(name: str) -> _BoarderLedger:
        return grouped.setdefault(name, _BoarderLedger())

    for entry in storage.list_ipoint_entries(conn):
        ledger_for(entry.normalized_name).entries.append(entry)
    for adjustment in storage.list_ipoint_adjustments(conn):
        ledger_for(adjustment.normalized_name).adjustments.append(adjustment)
    for confiscation in storage.list_ipoint_confiscations(conn):
        ledger_for(confiscation.normalized_name).confiscations.append(confiscation)
    return grouped


def _recorded_trigger_months(conn) -> dict[str, "frozenset[str]"]:
    """Maps each Match Key to the trigger months it ever had a Redemption for.

    Read from the audit ledger rather than the live rows, so a Removed
    Confiscation's month stays recorded and cannot be materialised afresh by a
    later read (story 27's "remove freely"; the audit preserves the prior state).
    """
    recorded: dict[str, set[str]] = {}
    for audit in storage.list_ipoint_audit(conn):
        if audit.entity_type != "confiscation" or audit.action != "created":
            continue
        if not audit.after_state:
            continue
        month = json.loads(audit.after_state).get("trigger_month")
        if month:
            recorded.setdefault(audit.normalized_name, set()).add(month)
    return {name: frozenset(months) for name, months in recorded.items()}


def pending_redemptions(
    conn, today: str | None = None
) -> list[tuple[str, PendingRedemption]]:
    """Returns every Boarder's materialisable pending Redemption, if any.

    A pure read: it evaluates the stored ledger and never writes, so a
    read-only connection can call it to decide whether materialisation is
    needed.
    """
    if today is None:
        today = today_iso()
    recorded = _recorded_trigger_months(conn)
    candidates: list[tuple[str, PendingRedemption]] = []
    for name, ledger in _ledger_by_boarder(conn).items():
        pending = evaluate_month_close(
            ledger.entries,
            ledger.adjustments,
            ledger.confiscations,
            today,
            recorded.get(name, frozenset()),
        )
        if pending is not None:
            candidates.append((name, pending))
    return candidates


def materialise_pending_redemptions(conn, today: str | None = None) -> int:
    """Persists each materialisable pending Redemption idempotently.

    Called from the read path under the shared mutation-retry seam when
    :func:`pending_redemptions` finds one, so the stored pending appears without
    a scheduler (ADR 0007). Returns how many rows were written.
    """
    if today is None:
        today = today_iso()
    stamp = datetime.now(tz=timezone.utc).isoformat()
    written = 0
    for name, pending in pending_redemptions(conn, today):
        try:
            with conn:
                if storage.get_open_ipoint_confiscation(conn, name) is not None:
                    continue
                confiscation_id = storage.stage_ipoint_confiscation(
                    conn,
                    name,
                    pending.trigger_month,
                    pending.points_redeemed,
                    pending.tier,
                    STATUS_PENDING,
                    stamp,
                )
                storage.stage_ipoint_audit(
                    conn,
                    _audit_draft(
                        "confiscation",
                        confiscation_id,
                        name,
                        "created",
                        None,
                        _confiscation_fields(
                            pending.trigger_month,
                            pending.points_redeemed,
                            pending.tier,
                            STATUS_PENDING,
                        ),
                        stamp,
                    ),
                )
            written += 1
        except sqlite3.IntegrityError:
            # A concurrent read materialised this Boarder first; the partial
            # unique index is the authority, so skip the duplicate.
            continue
    return written


def confirm_redemption(
    conn,
    confiscation_id: int,
    today: str | None = None,
    recorded_at: str | None = None,
) -> "RedemptionConfirmed | RedemptionRejected":
    """Confirms a pending Redemption into an active Phone Confiscation.

    Freezes the Boarder's display name and bed, records the fixed
    ``release_due``, and debits the Balance by ``points_redeemed``. Refused,
    writing nothing, when the Balance has fallen below the pending's points
    since materialisation (ADR 0006).
    """
    confiscation = storage.get_ipoint_confiscation(conn, confiscation_id)
    if confiscation is None:
        return RedemptionRejected(reason="That redemption no longer exists.")
    if confiscation.status != STATUS_PENDING:
        return RedemptionRejected(reason="That redemption is no longer pending.")

    if _balance_for(conn, confiscation.normalized_name) - confiscation.points_redeemed < 0:
        return RedemptionRejected(reason=_OVERDRAW)

    confirmed_on = _as_date(today or today_iso())
    release_due = release_due_for(confiscation.tier, confirmed_on).isoformat()
    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    identity = storage.resolve_boarder_identity(conn, confiscation.normalized_name)
    display_name = (
        identity.display_name if identity is not None else confiscation.normalized_name
    )
    bed = identity.bed if identity is not None else ""

    after = _confiscation_state(confiscation)
    after.update(
        {
            "status": STATUS_ACTIVE,
            "display_name": display_name,
            "bed": bed,
            "confirmed_at": stamp,
            "release_due": release_due,
        }
    )

    with conn:
        storage.stage_confirm_ipoint_confiscation(
            conn, confiscation_id, display_name, bed, stamp, release_due
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "confiscation",
                confiscation_id,
                confiscation.normalized_name,
                "confirmed",
                _confiscation_state(confiscation),
                after,
                stamp,
            ),
        )

    return RedemptionConfirmed(
        normalized_name=confiscation.normalized_name,
        display_name=display_name,
        points_redeemed=confiscation.points_redeemed,
        release_due=release_due,
    )


def void_confiscation(
    conn,
    confiscation_id: int,
    reason: str | None = None,
    recorded_at: str | None = None,
) -> "ConfiscationVoided | ConfiscationRejected":
    """Voids a pending Redemption or an active Confiscation.

    A pending row never debited the Balance, so voiding it merely cancels the
    reservation. An active row did debit, so voiding it returns its points to
    the Balance — a purely additive change, so no floor check is needed. A void
    of an active Confiscation requires a reason (story 26); a pending
    Redemption's reason stays optional.
    """
    confiscation = storage.get_ipoint_confiscation(conn, confiscation_id)
    if confiscation is None:
        return ConfiscationRejected(reason="That Confiscation no longer exists.")
    if confiscation.status not in _OPEN_STATUSES:
        return ConfiscationRejected(
            reason="That Confiscation is no longer pending or active."
        )

    clean_reason = (reason or "").strip() or None
    if confiscation.status == STATUS_ACTIVE and clean_reason is None:
        return ConfiscationRejected(
            reason="A reason is required to void a Confiscation."
        )

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, confiscation.normalized_name)

    after = _confiscation_state(confiscation)
    after.update(
        {"status": STATUS_VOIDED, "voided_at": stamp, "void_reason": clean_reason}
    )

    with conn:
        storage.stage_void_ipoint_confiscation(
            conn, confiscation_id, stamp, clean_reason
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "confiscation",
                confiscation_id,
                confiscation.normalized_name,
                "voided",
                _confiscation_state(confiscation),
                after,
                stamp,
            ),
        )

    return ConfiscationVoided(
        normalized_name=confiscation.normalized_name,
        display_name=display_name,
        points_redeemed=confiscation.points_redeemed,
        was_pending=confiscation.status == STATUS_PENDING,
    )


def release_confiscation(
    conn,
    confiscation_id: int,
    released_at: str | None = None,
) -> "ConfiscationReleased | ConfiscationRejected":
    """Marks an active Confiscation released, recording when the phone returned.

    Release is allowed at any time, including before ``release_due`` (story 25),
    and never blocks on the lateness gate: a Stacked Confiscation can be
    released, but the phone stays held while a lateness Phone Hold remains. The
    points stay debited (``released`` is still a confirmed status).
    """
    confiscation = storage.get_ipoint_confiscation(conn, confiscation_id)
    if confiscation is None:
        return ConfiscationRejected(reason="That Confiscation no longer exists.")
    if confiscation.status != STATUS_ACTIVE:
        return ConfiscationRejected(
            reason="Only an active Confiscation can be released."
        )

    stamp = released_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, confiscation.normalized_name)

    after = _confiscation_state(confiscation)
    after.update({"status": STATUS_RELEASED, "released_at": stamp})

    with conn:
        storage.stage_release_ipoint_confiscation(conn, confiscation_id, stamp)
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "confiscation",
                confiscation_id,
                confiscation.normalized_name,
                "released",
                _confiscation_state(confiscation),
                after,
                stamp,
            ),
        )

    return ConfiscationReleased(
        normalized_name=confiscation.normalized_name,
        display_name=display_name,
        points_redeemed=confiscation.points_redeemed,
        released_at=stamp,
    )


def edit_confiscation(
    conn,
    confiscation_id: int,
    tier,
    recorded_at: str | None = None,
) -> "ConfiscationEdited | ConfiscationRejected":
    """Corrects an active Confiscation's tier, and so its fixed period.

    The tier fixes both the redeemed points and the period (story 21), so the
    edit recomputes ``points_redeemed`` and ``release_due`` from the original
    ``confirmed_at`` date; the frozen display name and bed are untouched, and a
    pending row's locked tier (story 16) can never be edited. Raising the tier
    debits more and is refused when it would take the Balance below zero
    (ADR 0006); lowering it is always safe.
    """
    confiscation = storage.get_ipoint_confiscation(conn, confiscation_id)
    if confiscation is None:
        return ConfiscationRejected(reason="That Confiscation no longer exists.")
    if confiscation.status != STATUS_ACTIVE:
        return ConfiscationRejected(
            reason="Only an active Confiscation can be edited."
        )

    tier_value = _coerce_points(tier)
    if tier_value not in TIERS:
        return ConfiscationRejected(
            reason="Confiscation tier must be one of 5, 10, or 15."
        )

    if confiscation.confirmed_at is None:
        return ConfiscationRejected(reason="That Confiscation was never confirmed.")

    release_due = release_due_for(
        tier_value, _as_date(confiscation.confirmed_at)
    ).isoformat()
    # The active row is already subtracted from the Balance, so replacing its
    # debit means adding the old amount back before subtracting the new one.
    projected = (
        _balance_for(conn, confiscation.normalized_name)
        + confiscation.points_redeemed
        - tier_value
    )
    if projected < 0:
        return ConfiscationRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, confiscation.normalized_name)

    after = _confiscation_state(confiscation)
    after.update(
        {
            "tier": tier_value,
            "points_redeemed": tier_value,
            "release_due": release_due,
        }
    )

    with conn:
        storage.stage_update_ipoint_confiscation(
            conn, confiscation_id, tier_value, tier_value, release_due
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "confiscation",
                confiscation_id,
                confiscation.normalized_name,
                "edited",
                _confiscation_state(confiscation),
                after,
                stamp,
            ),
        )

    return ConfiscationEdited(
        normalized_name=confiscation.normalized_name,
        display_name=display_name,
        tier=tier_value,
        release_due=release_due,
    )


def remove_confiscation(
    conn,
    confiscation_id: int,
    recorded_at: str | None = None,
) -> "ConfiscationRemoved | ConfiscationRejected":
    """Hard-deletes a Confiscation of any status, auditing the prior state.

    Removing a confirmed (``active`` or ``released``) row returns its points to
    the Balance — additive, so no floor check is needed. The prior state
    survives in the I-Point Audit History.
    """
    confiscation = storage.get_ipoint_confiscation(conn, confiscation_id)
    if confiscation is None:
        return ConfiscationRejected(reason="That Confiscation no longer exists.")

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, confiscation.normalized_name)

    with conn:
        storage.stage_delete_ipoint_confiscation(conn, confiscation_id)
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "confiscation",
                confiscation_id,
                confiscation.normalized_name,
                "removed",
                _confiscation_state(confiscation),
                None,
                stamp,
            ),
        )

    return ConfiscationRemoved(
        normalized_name=confiscation.normalized_name,
        display_name=display_name,
        points_redeemed=confiscation.points_redeemed,
    )


def _validate_adjustment(
    normalized_name: str, points, reason: str
) -> tuple[str, int, str] | AdjustmentRejected:
    """Validates the shared Adjustment fields: name, non-zero points, reason."""
    name = (normalized_name or "").strip()
    if not name:
        return AdjustmentRejected(reason="A boarder name is required.")

    points_value = _coerce_signed_points(points)
    if points_value is None or points_value == 0:
        return AdjustmentRejected(
            reason="Adjustment points must be a non-zero whole number."
        )

    clean_reason = (reason or "").strip()
    if not clean_reason:
        return AdjustmentRejected(reason="A reason is required.")

    return name, points_value, clean_reason


def add_adjustment(
    conn,
    normalized_name: str,
    points,
    reason: str,
    recorded_at: str | None = None,
) -> AdjustmentSaved | AdjustmentRejected:
    """Validates and adds one signed I-Point Adjustment, auditing it atomically.

    A positive Adjustment adds to the Balance; a subtraction may never take the
    Balance below zero (ADR 0006), so it is capped at the current Balance. A
    rejected submission writes nothing.
    """
    validated = _validate_adjustment(normalized_name, points, reason)
    if isinstance(validated, AdjustmentRejected):
        return validated
    name, points_value, clean_reason = validated

    if _balance_for(conn, name) + points_value < 0:
        return AdjustmentRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, name)

    with conn:
        adjustment_id = storage.stage_ipoint_adjustment(
            conn, name, points_value, clean_reason, stamp
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "adjustment",
                adjustment_id,
                name,
                "created",
                None,
                _adjustment_fields(points_value, clean_reason),
                stamp,
            ),
        )

    return AdjustmentSaved(
        normalized_name=name,
        display_name=display_name,
        points=points_value,
        reason=clean_reason,
    )


def log_entry(
    conn,
    normalized_name: str,
    points,
    occurred_on: str | None,
    reason: str,
    recorded_at: str | None = None,
    today: str | None = None,
) -> EntrySaved | EntryRejected:
    """Validates and logs one I-Point Entry, writing its audit row atomically.

    Points must be a positive whole number and the reason must be present;
    a blank date falls back to the injected (or machine-local) today. A
    rejected submission writes nothing.
    """
    name = (normalized_name or "").strip()
    if not name:
        return EntryRejected(reason="A boarder name is required.")

    points_value = _coerce_points(points)
    if points_value is None or points_value <= 0:
        return EntryRejected(
            reason="Entry points must be a positive whole number."
        )

    clean_reason = (reason or "").strip()
    if not clean_reason:
        return EntryRejected(reason="A reason is required.")

    resolved_date = _resolve_occurred_on(occurred_on, today)
    if resolved_date is None:
        return EntryRejected(reason="Enter a valid date.")

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, name)

    with conn:
        entry_id = storage.stage_ipoint_entry(
            conn, name, points_value, resolved_date, clean_reason, stamp
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "entry",
                entry_id,
                name,
                "created",
                None,
                _entry_fields(points_value, resolved_date, clean_reason),
                stamp,
            ),
        )

    return EntrySaved(
        normalized_name=name,
        display_name=display_name,
        points=points_value,
        occurred_on=resolved_date,
    )


def edit_entry(
    conn,
    entry_id: int,
    points,
    occurred_on: str | None,
    reason: str,
    recorded_at: str | None = None,
) -> "EntryEdited | EntryRejected":
    """Validates and applies an Entry correction, auditing the prior state.

    Points must remain a positive whole number, the reason present, and the
    date valid. A blank date is rejected rather than defaulted, since the form
    always carries the entry's current date. A change that would drive the
    Balance below zero is refused (ADR 0006), writing nothing.
    """
    entry = storage.get_ipoint_entry(conn, entry_id)
    if entry is None:
        return EntryRejected(reason="That I-Point Entry no longer exists.")

    points_value = _coerce_points(points)
    if points_value is None or points_value <= 0:
        return EntryRejected(reason="Entry points must be a positive whole number.")

    clean_reason = (reason or "").strip()
    if not clean_reason:
        return EntryRejected(reason="A reason is required.")

    resolved_date = _parse_occurred_on(occurred_on)
    if resolved_date is None:
        return EntryRejected(reason="Enter a valid date.")

    if _balance_for(conn, entry.normalized_name) - entry.points + points_value < 0:
        return EntryRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, entry.normalized_name)

    with conn:
        storage.stage_update_ipoint_entry(
            conn, entry_id, points_value, resolved_date, clean_reason
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "entry",
                entry_id,
                entry.normalized_name,
                "edited",
                _entry_state(entry),
                _entry_fields(points_value, resolved_date, clean_reason),
                stamp,
            ),
        )

    return EntryEdited(
        normalized_name=entry.normalized_name,
        display_name=display_name,
        points=points_value,
        occurred_on=resolved_date,
    )


def remove_entry(
    conn,
    entry_id: int,
    recorded_at: str | None = None,
) -> "EntryRemoved | EntryRejected":
    """Removes an Entry from the live ledger, auditing the prior state.

    The row leaves the Balance but its snapshot survives in the I-Point Audit
    History. A removal that would drive the Balance below zero is refused
    (ADR 0006), writing nothing.
    """
    entry = storage.get_ipoint_entry(conn, entry_id)
    if entry is None:
        return EntryRejected(reason="That I-Point Entry no longer exists.")

    if _balance_for(conn, entry.normalized_name) - entry.points < 0:
        return EntryRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, entry.normalized_name)

    with conn:
        storage.stage_delete_ipoint_entry(conn, entry_id)
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "entry",
                entry_id,
                entry.normalized_name,
                "removed",
                _entry_state(entry),
                None,
                stamp,
            ),
        )

    return EntryRemoved(
        normalized_name=entry.normalized_name,
        display_name=display_name,
        points=entry.points,
        occurred_on=entry.occurred_on,
    )


def edit_adjustment(
    conn,
    adjustment_id: int,
    points,
    reason: str,
    recorded_at: str | None = None,
) -> "AdjustmentEdited | AdjustmentRejected":
    """Validates and applies an Adjustment edit, auditing the prior state.

    The new signed value must be non-zero and its reason present. A change that
    would drive the Balance below zero is refused (ADR 0006), writing nothing.
    """
    adjustment = storage.get_ipoint_adjustment(conn, adjustment_id)
    if adjustment is None:
        return AdjustmentRejected(reason="That I-Point Adjustment no longer exists.")

    validated = _validate_adjustment(adjustment.normalized_name, points, reason)
    if isinstance(validated, AdjustmentRejected):
        return validated
    _, points_value, clean_reason = validated

    projected = _balance_for(conn, adjustment.normalized_name) - adjustment.points
    if projected + points_value < 0:
        return AdjustmentRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, adjustment.normalized_name)

    with conn:
        storage.stage_update_ipoint_adjustment(
            conn, adjustment_id, points_value, clean_reason
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "adjustment",
                adjustment_id,
                adjustment.normalized_name,
                "edited",
                _adjustment_state(adjustment),
                _adjustment_fields(points_value, clean_reason),
                stamp,
            ),
        )

    return AdjustmentEdited(
        normalized_name=adjustment.normalized_name,
        display_name=display_name,
        points=points_value,
        reason=clean_reason,
    )


def remove_adjustment(
    conn,
    adjustment_id: int,
    recorded_at: str | None = None,
) -> "AdjustmentRemoved | AdjustmentRejected":
    """Removes an Adjustment from the ledger, auditing the prior state.

    A removal that would drive the Balance below zero is refused (ADR 0006),
    writing nothing.
    """
    adjustment = storage.get_ipoint_adjustment(conn, adjustment_id)
    if adjustment is None:
        return AdjustmentRejected(reason="That I-Point Adjustment no longer exists.")

    projected = _balance_for(conn, adjustment.normalized_name) - adjustment.points
    if projected < 0:
        return AdjustmentRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, adjustment.normalized_name)

    with conn:
        storage.stage_delete_ipoint_adjustment(conn, adjustment_id)
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "adjustment",
                adjustment_id,
                adjustment.normalized_name,
                "removed",
                _adjustment_state(adjustment),
                None,
                stamp,
            ),
        )

    return AdjustmentRemoved(
        normalized_name=adjustment.normalized_name,
        display_name=display_name,
        points=adjustment.points,
        reason=adjustment.reason,
    )


def _describe_change(action: str, before, after, entity_type: str) -> str:
    """Words one audit row's prior and new state for the history table."""
    if entity_type == "confiscation":
        if action == "created" and after:
            return (
                f"Pending redemption of {after['points_redeemed']} points "
                f"(tier {after['tier']}) from {after['trigger_month']}"
            )
        if action == "confirmed" and after:
            return (
                f"Confirmed redemption of {after['points_redeemed']} points; "
                f"phone due back {after['release_due']}"
            )
        if action == "voided":
            reason = (after or {}).get("void_reason")
            was_pending = (before or {}).get("status") == STATUS_PENDING
            base = "Voided the pending redemption" if was_pending else (
                "Voided the Confiscation"
            )
            return f"{base}: {reason}" if reason else base
        if action == "released" and after:
            return (
                f"Released the Confiscation of {after['points_redeemed']} points "
                f"({after.get('released_at') or ''})"
            )
        if action == "edited" and before and after:
            return (
                f"Tier {before['tier']} -> {after['tier']}; "
                f"phone due {before.get('release_due')} -> {after.get('release_due')}"
            )
        if action == "removed" and before:
            return (
                f"Removed the Confiscation of {before['points_redeemed']} points"
            )
        return action
    if entity_type == "adjustment":
        if action == "created" and after:
            return f"Created adjustment of {after['points']}: {after['reason']}"
        if action == "removed" and before:
            return f"Removed adjustment of {before['points']}: {before['reason']}"
        if action == "edited" and before and after:
            return (
                f"{before['points']} -> {after['points']} points; "
                f"{before['reason']} -> {after['reason']}"
            )
        return action
    if action == "created" and after:
        return f"Created {after['points']} on {after['occurred_on']}: {after['reason']}"
    if action == "removed" and before:
        return f"Removed {before['points']} on {before['occurred_on']}: {before['reason']}"
    if action == "edited" and before and after:
        return (
            f"{before['points']} -> {after['points']} points; "
            f"{before['occurred_on']} -> {after['occurred_on']}; "
            f"{before['reason']} -> {after['reason']}"
        )
    return action


def _audit_note(audit: IPointAudit) -> IPointAuditNote:
    """Renders one stored Audit row into a display line."""
    before = json.loads(audit.before_state) if audit.before_state else None
    after = json.loads(audit.after_state) if audit.after_state else None
    return IPointAuditNote(
        action=audit.action.capitalize(),
        changed_at=audit.changed_at,
        description=_describe_change(audit.action, before, after, audit.entity_type),
    )


def phone_held_keys(conn) -> set[str]:
    """Returns the Match Keys whose lateness Punishment currently holds a phone.

    The single read that feeds the Stacked predicate; keeping it here means the
    I-Points lifecycle never imports the Punishments module.
    """
    return {
        punishment.normalized_name
        for punishment in storage.list_punishments(
            conn, statuses=(_PHONE_HELD_PUNISHMENT,)
        )
    }


def attach_confiscation_flags(
    confiscations: list[Confiscation],
    phone_held_keys: set[str],
    today: str,
) -> list[Confiscation]:
    """Attaches computed ``is_due``/``stacked`` flags to each Confiscation.

    A pure function with ``today`` and the phone-held Match Keys injected, so
    the boundary is deterministic in tests (the pattern the Punishments module
    uses). ``is_due`` marks an active Confiscation on or after its
    ``release_due``; ``stacked`` marks one whose Boarder also has a lateness
    Phone Hold. Both are display flags — the app never releases on its own.
    """
    today_date = _as_date(today)
    for confiscation in confiscations:
        active = confiscation.status == STATUS_ACTIVE
        confiscation.is_due = (
            active
            and confiscation.release_due is not None
            and _as_date(confiscation.release_due) <= today_date
        )
        confiscation.stacked = (
            active and confiscation.normalized_name in phone_held_keys
        )
    return confiscations


# Active Confiscations lead the list; released then voided follow.
_CONFISCATION_STATUS_RANK = {
    STATUS_ACTIVE: 0,
    STATUS_RELEASED: 1,
    STATUS_VOIDED: 2,
}


def confiscation_list(
    conn,
    statuses: tuple[str, ...] | None = None,
    today: str | None = None,
) -> list[Confiscation]:
    """Lists Confiscations across Boarders for the filterable management list.

    ``statuses`` narrows the query (``None`` lists every status); rows carry the
    derived due/Stacked flags, and sort active-first by release date.
    """
    if today is None:
        today = today_iso()
    rows = storage.list_ipoint_confiscations(conn, statuses=statuses)
    attach_confiscation_flags(rows, phone_held_keys(conn), today)
    return sorted(
        rows,
        key=lambda row: (
            _CONFISCATION_STATUS_RANK.get(row.status, 99),
            row.release_due or "",
            row.id,
        ),
    )


def _summary_for(
    key: str,
    ledger: _BoarderLedger,
    who: BoarderIdentity | None,
    held: set[str],
    audits: list[IPointAudit],
    today: str,
) -> IPointSummary:
    """Builds one boarder's I-Point Summary from an already-read ledger.

    The single owner of the derived shape: Balance, chronologically ordered
    entries, the open pending Redemption, and Confiscation display flags. Both
    :func:`boarder_balances` and :func:`boarder_summary` call it, so the
    arithmetic and flag logic cannot drift between the list and one profile.
    """
    pending = next(
        (row for row in ledger.confiscations if row.status == STATUS_PENDING),
        None,
    )
    return IPointSummary(
        normalized_name=key,
        display_name=who.display_name if who else key,
        bed=who.bed if who else "",
        balance=_net_balance(
            ledger.entries, ledger.adjustments, ledger.confiscations
        ),
        entries=sorted(ledger.entries, key=lambda row: (row.occurred_on, row.id)),
        adjustments=ledger.adjustments,
        audits=[_audit_note(audit) for audit in audits],
        pending=pending,
        confiscations=attach_confiscation_flags(ledger.confiscations, held, today),
    )


def boarder_balances(
    conn, today: str | None = None
) -> list[IPointSummary]:
    """Derives each boarder's I-Point Balance and Confiscation state.

    Balance is that Match Key's Entries plus Adjustments minus confirmed
    Redemptions — never stored, so it cannot drift. A pending Redemption is
    surfaced separately and does not debit the Balance (ADR 0007). Identity
    fields resolve freshest-first through the All-Time List, falling back to the
    Match Key for a boarder known only through I-Points. Summaries sort by the
    shared Bed rule then name.
    """
    if today is None:
        today = today_iso()
    ledgers = _ledger_by_boarder(conn)
    identity = storage.freshest_identity_map(conn)
    held = phone_held_keys(conn)

    grouped_audits: dict[str, list[IPointAudit]] = {}
    for audit in storage.list_ipoint_audit(conn):
        grouped_audits.setdefault(audit.normalized_name, []).append(audit)

    summaries = [
        _summary_for(
            key,
            ledgers.get(key, _BoarderLedger()),
            identity.get(key),
            held,
            grouped_audits.get(key, []),
            today,
        )
        for key in ledgers.keys() | grouped_audits.keys()
    ]
    summaries.sort(key=lambda summary: (bed_sort_key(summary.bed), summary.display_name))
    return summaries


def boarder_summary(
    conn, normalized_name: str, today: str | None = None
) -> IPointSummary | None:
    """Derives one boarder's I-Points position for the Boarder Profile.

    The scoped counterpart to :func:`boarder_balances`: it reads only the one
    Match Key's ledger and audit rows, rather than every Boarder's. ``None``
    means the key is unknown to I-Points — it has no row of any kind — so the
    profile can fall back to its own empty state. Identity resolves
    freshest-first, falling back to the Match Key for a Boarder known only
    through I-Points; Balance, pending, and Confiscation flags come from the
    shared :func:`_summary_for`, so they cannot drift from the full list.
    """
    if today is None:
        today = today_iso()
    entries = storage.list_ipoint_entries(conn, normalized_name)
    adjustments = storage.list_ipoint_adjustments(conn, normalized_name)
    confiscations = storage.list_ipoint_confiscations(conn, normalized_name)
    audits = storage.list_ipoint_audit(conn, normalized_name)
    if not (entries or adjustments or confiscations or audits):
        return None
    ledger = _BoarderLedger(
        entries=entries, adjustments=adjustments, confiscations=confiscations
    )
    who = storage.freshest_identity_map(conn).get(normalized_name)
    return _summary_for(
        normalized_name, ledger, who, phone_held_keys(conn), audits, today
    )
