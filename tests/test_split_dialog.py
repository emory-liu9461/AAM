from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting.books import Books, ParsedTransaction


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_split_one_transaction_over_several_categories(app, tmp_path, monkeypatch):
    from am_accounting.gui import transaction_dialog as td

    monkeypatch.setattr(td, "error", lambda *a, **k: None)
    b = Books.open(tmp_path / "s.amdb", create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 3, 2), "HOME DEPOT", -500_00)])
    tx = b.transactions()[0]
    exp = [a for a in b.accounts() if a.type == "expense"][:2]
    d = td.SplitDialog(b, tx.id)
    assert d.table.rowCount() == 2 and d.assigned() == 500_00
    d.table.cellWidget(0, 0).setCurrentIndex(d.table.cellWidget(0, 0).findData(exp[0].id))
    d.table.cellWidget(0, 1).setValue(320)
    d.table.cellWidget(1, 0).setCurrentIndex(d.table.cellWidget(1, 0).findData(exp[1].id))
    d.save()
    assert not d.result()                                  # 320 of 500 assigned: not saved
    d.table.setCurrentCell(1, 0)
    d.rest_here()
    d.table.cellWidget(1, 2).setText("paint")
    d.save()
    assert d.result()
    assert b.splits(tx.id) == [(exp[0].id, 320_00, "", ""), (exp[1].id, 180_00, "paint", "")]
    totals = b.ledger_totals()
    assert totals[exp[0].id] == 320_00 and totals[exp[1].id] == 180_00 and totals[bank] == -500_00
    d = td.SplitDialog(b, tx.id)                            # opens with the split; back to one category
    assert d.table.rowCount() == 2
    d.table.cellWidget(0, 1).setValue(500)
    d.table.cellWidget(1, 1).setValue(0)
    d.save()
    assert b.splits(tx.id) == [] and b.transaction(tx.id).category_id == exp[0].id
    b.close()
