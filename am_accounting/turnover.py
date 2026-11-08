"""Unit turnover: each time a unit changes hands — when the tenant moved out, how long it sat empty, what it cost to
turn over and the rent before and after.

A turnover is recorded **automatically** when a unit on the rent roll is edited:

* occupied → vacant: a turnover starts (moved out today, the old tenant and rent kept);
* vacant → occupied: the open turnover for the unit is finished (moved in on the new move-in date, the new rent);
* a different tenant straight away: a turnover with no days empty.

They can also be entered or changed by hand. **Cost to turn over** is what was spent on the unit's in-suite
renovation line (CapEx; its account in the books) from the move-out month to the move-in month, plus any other cost
entered by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_turns (
    id           INTEGER PRIMARY KEY,
    holding_id   INTEGER NOT NULL,
    unit         TEXT NOT NULL,
    move_out     TEXT,
    prev_tenant  TEXT NOT NULL DEFAULT '',
    prev_rent    INTEGER NOT NULL DEFAULT 0,
    move_in      TEXT,                       -- NULL while it's still empty
    new_tenant   TEXT NOT NULL DEFAULT '',
    new_rent     INTEGER NOT NULL DEFAULT 0,
    extra_cost   INTEGER NOT NULL DEFAULT 0, -- cost to turn over not on the capex schedule
    notes        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_unit_turns ON unit_turns(holding_id, unit);
"""


@dataclass
class Turn:
    unit: str
    move_out: str | None = None
    prev_tenant: str = ""
    prev_rent: int = 0
    move_in: str | None = None
    new_tenant: str = ""
    new_rent: int = 0
    extra_cost: int = 0
    notes: str = ""
    id: int | None = None
    capex_cost: int = 0          # from the capex schedule / books (filled by with_costs)

    @property
    def open(self) -> bool:
        return not self.move_in

    def days_vacant(self, as_of: date | None = None) -> int | None:
        if not self.move_out:
            return None
        end = date.fromisoformat(self.move_in) if self.move_in else (as_of or date.today())
        return max(0, (end - date.fromisoformat(self.move_out)).days)

    @property
    def cost(self) -> int:
        return self.capex_cost + self.extra_cost

    @property
    def rent_change(self) -> int | None:
        return self.new_rent - self.prev_rent if self.move_in and self.prev_rent and self.new_rent else None

    @property
    def rent_change_pct(self) -> float | None:
        c = self.rent_change
        return c / self.prev_rent * 100 if c is not None else None

    def lost_rent(self, as_of: date | None = None) -> int:
        """Rent not collected while it was empty (at the old rent)."""
        d = self.days_vacant(as_of)
        return round(self.prev_rent * 12 / 365 * d) if d else 0


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


def turns(pf, holding_id: int) -> list[Turn]:
    ready(pf)
    return [Turn(r["unit"], r["move_out"], r["prev_tenant"], r["prev_rent"], r["move_in"], r["new_tenant"],
                 r["new_rent"], r["extra_cost"], r["notes"], r["id"])
            for r in pf.conn.execute("SELECT * FROM unit_turns WHERE holding_id=? "
                                     "ORDER BY COALESCE(move_out, move_in) DESC, id DESC", (holding_id,))]


def save(pf, holding_id: int, t: Turn) -> int:
    ready(pf)
    if not t.unit.strip():
        raise ValueError("Enter the unit")
    for d in (t.move_out, t.move_in):
        if d:
            date.fromisoformat(d)
    if not (t.move_out or t.move_in):
        raise ValueError("Enter the move-out or the move-in date")
    if t.move_out and t.move_in and t.move_in < t.move_out:
        raise ValueError("The move-in date is before the move-out date")
    if min(t.prev_rent, t.new_rent, t.extra_cost) < 0:
        raise ValueError("Amounts can't be negative")
    vals = (holding_id, t.unit.strip(), t.move_out, t.prev_tenant.strip(), t.prev_rent, t.move_in or None,
            t.new_tenant.strip(), t.new_rent, t.extra_cost, t.notes.strip())
    with pf.conn:
        if t.id is None:
            t.id = pf.conn.execute("INSERT INTO unit_turns(holding_id, unit, move_out, prev_tenant, prev_rent, move_in, "
                                   "new_tenant, new_rent, extra_cost, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   vals).lastrowid
        else:
            pf.conn.execute("UPDATE unit_turns SET holding_id=?, unit=?, move_out=?, prev_tenant=?, prev_rent=?, "
                            "move_in=?, new_tenant=?, new_rent=?, extra_cost=?, notes=? WHERE id=?", vals + (t.id,))
    return t.id


def delete(pf, turn_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM unit_turns WHERE id=?", (turn_id,))


def record_change(pf, holding_id: int, old, new, on: date | None = None) -> Turn | None:
    """A unit was edited (``old`` → ``new``, both operations.Unit; ``old`` None for a new unit): record a move-out,
    a move-in or both. Returns the turnover changed, if any."""
    on_iso = (on or date.today()).isoformat()
    if old is None:
        return None
    key = new.unit.strip().lower()
    open_ = next((t for t in turns(pf, holding_id) if t.open and t.unit.strip().lower() == key), None)
    rent = lambda u: u.rent_cents + u.parking_cents + u.locker_cents  # noqa: E731
    moved_in = new.move_in if new.move_in and new.move_in != old.move_in else None
    if old.occupied and not new.occupied:                         # moved out
        if open_:
            return None
        t = Turn(new.unit, on_iso, old.tenant, rent(old))
    elif not old.occupied and new.occupied:                       # moved in
        if open_ is None:
            t = Turn(new.unit, None, "", 0)
        else:
            t = open_
        t.move_in, t.new_tenant, t.new_rent = moved_in or on_iso, new.tenant, rent(new)
        if t.move_out and t.move_in < t.move_out:
            t.move_in = t.move_out
    elif old.occupied and new.occupied and old.tenant.strip().lower() != new.tenant.strip().lower() and old.tenant:
        day = new.move_in if new.move_in and new.move_in != old.move_in else on_iso
        t = Turn(new.unit, day, old.tenant, rent(old), day, new.tenant, rent(new))
    else:
        return None
    save(pf, holding_id, t)
    return t


def with_costs(pf, holding, rows: list[Turn], as_of: date | None = None) -> list[Turn]:
    """Each turnover's cost from the unit's in-suite renovation line (CapEx and its account in the books), from the
    move-out month to the move-in month (or to now while it's still empty)."""
    from . import capex as cx

    try:
        lines = cx.lines(pf, holding, as_of)
    except Exception:  # noqa: BLE001
        lines = []
    by_unit = {ln.unit.strip().lower(): ln for ln in lines if ln.unit}
    today = (as_of or date.today()).isoformat()
    for t in rows:
        ln = by_unit.get(t.unit.strip().lower())
        t.capex_cost = 0
        if ln is None or not (t.move_out or t.move_in):
            continue
        start = (t.move_out or t.move_in)[:7]
        end = (t.move_in or today)[:7]
        t.capex_cost = sum(v for m, v in (ln.monthly or {}).items() if start <= m <= end)
    return rows


@dataclass
class Summary:
    turns_12: int = 0            # turnovers started in the last 12 months
    vacant_now: int = 0          # still empty
    avg_days: float | None = None
    avg_cost: int | None = None
    avg_lift_pct: float | None = None
    lost_rent_12: int = 0


def summary(rows: list[Turn], as_of: date | None = None) -> Summary:
    as_of = as_of or date.today()
    since = (as_of - timedelta(days=365)).isoformat()
    recent = [t for t in rows if (t.move_out or t.move_in or "") >= since]
    done = [t for t in recent if not t.open and t.move_out]
    lifts = [t.rent_change_pct for t in recent if t.rent_change_pct is not None]
    costs = [t.cost for t in recent if not t.open]
    return Summary(len(recent), sum(1 for t in rows if t.open),
                   sum(t.days_vacant(as_of) for t in done) / len(done) if done else None,
                   round(sum(costs) / len(costs)) if costs else None,
                   sum(lifts) / len(lifts) if lifts else None,
                   sum(t.lost_rent(as_of) for t in recent))
