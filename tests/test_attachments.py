import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import attachments as at
from am_accounting import portfolio as pfm
from am_accounting.books import Books


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_attach_list_remove(tmp_path):
    b = Books.open(tmp_path / "Maple.amdb")
    try:
        base = at.books_folder(b)
        assert base == tmp_path / "Maple Attachments"
        src = tmp_path / "Hydro bill: Oct?.pdf"
        src.write_bytes(b"%PDF-1.4 bill")
        a = at.add(b.conn, base, "transaction", 7, src, sub="2026")
        again = at.add(b.conn, base, "transaction", 7, src, sub="2026")
        assert a.exists and again.exists and a.path != again.path
        assert a.path.parent == base / "Transactions" / "2026" and a.name == "Hydro bill: Oct?.pdf"
        assert [x.id for x in at.listed(b.conn, base, "transaction", 7)] == [a.id, again.id]
        assert at.counts(b.conn, "transaction") == {"7": 2}
        src.unlink()
        assert at.listed(b.conn, base, "transaction", "7")[0].exists          # a copy: the original can go
        at.remove(b.conn, a)
        assert not a.path.exists() and len(at.listed(b.conn, base, "transaction", 7)) == 1
        with pytest.raises(ValueError):
            at.add(b.conn, base, "transaction", 7, tmp_path / "nope.pdf")
    finally:
        b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    assert at.portfolio_folder(pf) == tmp_path / "Portfolio Attachments"
    pf.close()


def test_attachments_in_windows(app, tmp_path, monkeypatch):
    from am_accounting import deals as dl
    from am_accounting import operations as op
    from am_accounting.gui import attachments_box as ab
    from am_accounting.gui import deals_tab as dt
    from am_accounting.gui import operations_tab as ot
    from am_accounting.gui.transaction_dialog import TransactionDialog

    monkeypatch.setattr(ab, "confirm", lambda *a: True)
    f = tmp_path / "lease.pdf"
    f.write_bytes(b"%PDF lease")
    b = Books.open(tmp_path / "Maple.amdb")
    try:
        tid = b.add_manual_transaction(b.account_by_code("1000").id, "2026-03-01", "HYDRO", -100_00)
        dlg = TransactionDialog(b, tid)
        assert dlg.docs.add_files([str(f)]) == 1 and dlg.docs.list.count() == 1
        dlg.docs.list.setCurrentRow(0)
        dlg.docs.remove()
        assert at.listed(b.conn, at.books_folder(b), "transaction", tid) == []
    finally:
        b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("45 Elm St")
    dl.save(pf, d)
    dd = dt.DealDialog(pf, dl.deal(pf, d.id))
    assert dd.docs.add_files([str(f)]) == 1
    assert at.listed(pf.conn, at.portfolio_folder(pf), "deal", d.id)[0].path.parent.name == "45 Elm St"
    assert isinstance(dt.DealDialog(pf).docs, dt.QLabel)                     # a new deal: save it first
    h = pfm.Holding("Elm")
    pf.save_holding(h)
    u = op.Unit("101", "Ann")
    op.save_unit(pf, h.id, u)
    ud = ot.UnitDialog(pf, h, u)
    assert ud.docs is not None and ud.docs.add_files([str(f)]) == 1
    assert ot.UnitDialog(pf, h).docs is None
    pf.close()
