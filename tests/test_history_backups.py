from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import backups as bk
from am_accounting import history as hs
from am_accounting import portfolio as pfm
from am_accounting.books import Books


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_change_history_in_books_and_portfolio(tmp_path):
    b = Books.open(tmp_path / "a.amdb")
    try:
        assert hs.changes(b.conn) == []                              # setting up the chart isn't logged
        a = b.add_account("6555", "Test Exp", "expense", "operating")
        b.update_account(a.id, code="6556", name="Test Expense", type_="expense", subtype="operating")
        cash = b.account_by_code("1000").id
        tid = b.add_manual_transaction(cash, "2026-01-01", "HYDRO ONE", -100_00)
        b.set_category(tid, a.id)
        b.set_category(tid, a.id)                                    # no change: not logged again
        e = b.add_journal_entry("2026-01-02", "adjustment", [(a.id, 5_00, 0), (cash, 0, 5_00)])
        b.delete_journal_entry(e)
        rows = hs.changes(b.conn)
        assert [(c.what, c.action) for c in rows] == [
            ("Journal entry", "deleted"), ("Journal entry", "added"), ("Bank transaction", "changed"),
            ("Bank transaction", "added"), ("Account", "changed"), ("Account", "added")]
        assert rows[2].summary == "2026-01-01 · HYDRO ONE · -100.00 — Category: (none) → Test Expense"
        assert rows[4].summary.endswith("Number: 6555 → 6556; Name: Test Exp → Test Expense")
        assert rows[0].who == hs.current_user()
        assert [c.action for c in hs.changes(b.conn, search="hydro")] == ["changed", "added"]
        assert "Account" in hs.kinds(b.conn)
    finally:
        b.close()
    # opening again doesn't rewrite the file
    import os
    before = os.stat(tmp_path / "a.amdb").st_mtime_ns
    Books.open(tmp_path / "a.amdb", create=False).close()
    assert os.stat(tmp_path / "a.amdb").st_mtime_ns == before
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Elm Street")
    pf.save_holding(h)
    from am_accounting import operations as op
    u = op.Unit("101", "Ann", rent_cents=1_000_00)
    op.save_unit(pf, h.id, u)
    u.rent_cents = 1_050_00
    op.save_unit(pf, h.id, u)
    rows = hs.changes(pf.conn)
    assert rows[0].what == "Unit" and rows[0].summary.startswith("Elm Street: 101")
    assert rows[0].summary.endswith("Rent: 1000.00 → 1050.00")
    pf.close()


def test_daily_backups_and_restore(tmp_path):
    b = Books.open(tmp_path / "Maple.amdb")
    try:
        b.add_account("6555", "Before", "expense", "operating")
        root = bk.backup_root(b.path)
        assert root == tmp_path / "Backups"
        p = bk.daily(b.conn, b.path, "Maple Ltd", root, today=date(2026, 11, 8))
        assert p is not None and p.parent == root / "Daily" and p.name.startswith("Maple Ltd 2026-11-08")
        assert bk.daily(b.conn, b.path, "Maple Ltd", root, today=date(2026, 11, 8)) is None    # once a day
        for d in range(1, 4):
            bk.daily(b.conn, b.path, "Maple Ltd", root, today=date(2026, 10, d), keep=3)
        assert len(list((root / "Daily").glob("*.amdb"))) == 3                                  # the last 3 kept
        b.add_account("6556", "After", "expense", "operating")
        lst = bk.backups(b.path, "Maple Ltd", root)
        assert lst[0].kind == "Daily" and lst[0].when.date() == date(2026, 11, 8)
        before = bk.restore_into(b.conn, b.path, lst[0].path, "Maple Ltd", root)
        b._invalidate()
        names = {a.name for a in b.accounts()}
        assert "Before" in names and "After" not in names                     # back to the backup
        assert before.parent.name == "Before Restore" and bk.check(before) == ""
        assert "Before restore" in {x.kind for x in bk.backups(b.path, "Maple Ltd", root)}
        (tmp_path / "junk.amdb").write_bytes(b"not a database")
        with pytest.raises(ValueError):
            bk.restore_into(b.conn, b.path, tmp_path / "junk.amdb", "Maple Ltd", root)
    finally:
        b.close()
    bk.restore(tmp_path / "Maple.amdb", before, "Maple Ltd", root)            # undo, with the file closed
    b = Books.open(tmp_path / "Maple.amdb", create=False)
    try:
        assert "After" in {a.name for a in b.accounts()}
    finally:
        b.close()


def test_history_and_backup_windows(app, tmp_path, monkeypatch):
    from am_accounting.gui import history_dialog as hd

    monkeypatch.setattr(hd, "confirm", lambda *a: True)
    monkeypatch.setattr(hd, "info", lambda *a: None)
    b = Books.open(tmp_path / "Maple.amdb")
    try:
        b.add_account("6555", "Gone Soon", "expense", "operating")
        h = hd.HistoryDialog(b.conn, "Maple")
        assert h.table.rowCount() == 1 and h.table.item(0, 3).text() == "added"
        h.search.setText("nothing like this")
        assert h.table.rowCount() == 0
        restored = []
        d = hd.BackupsDialog(b.path, "Maple", root=tmp_path / "bk",
                             on_restore=lambda p: restored.append(p) or "before.amdb")
        assert d.table.rowCount() == 0 and not d.restore_btn.isEnabled()
        assert d.backup_now() is not None and d.table.rowCount() == 1
        d.restore()
        assert len(restored) == 1
    finally:
        b.close()
