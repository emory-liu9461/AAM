"""Lender submission package: the documents a lender asks for, copied out of the deal folder."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from am_accounting import deal_folders as df
from am_accounting import deals as dl
from am_accounting import lender_package as lpk
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _touch(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _deal(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("Cedar Court", address="45 Cedar Ave, Oshawa")
    dl.save(pf, d)
    f = tmp_path / "45 Cedar"
    df.set_folder_of(pf, d, f)
    df.create(f, df.context(pf, d))
    uw = f / "Underwriting"
    _touch(uw / "Legal and Admin/Purchase and Sales Agreement/45 Cedar APS signed.pdf")
    _touch(uw / "Legal and Admin/Purchase and Sales Agreement/Archive/45 Cedar APS draft.pdf")
    _touch(uw / "Legal and Admin/Purchase and Sales Agreement/Initial Letter of Intent/45 Cedar LOI.pdf")
    _touch(uw / "Due Diligence Files/Rent Roll Oct 2026.xlsx")
    _touch(uw / "Due Diligence Files/Release form.pdf")                   # not a lease
    _touch(uw / "Due Diligence Files/2025 Property Tax Bill.pdf")
    _touch(uw / "Contracts and Quotes/Insurance/Quote/Intact quote.pdf")
    _touch(uw / "Pictures/Original (Provided) Photos/front.jpg")
    return pf, d, f


def test_template_has_the_package_folder(tmp_path):
    pf, d, f = _deal(tmp_path)
    assert (f / lpk.PACKAGES).is_dir()
    pf.close()


def test_candidates_and_generate(tmp_path):
    pf, d, f = _deal(tmp_path)
    picks = {pk.doc.name: pk for pk in lpk.candidates(f)}
    assert [c.path.name for c in picks["Purchase and Sale Agreement"].files] == ["45 Cedar APS signed.pdf"]  # no Archive
    assert [c.path.name for c in picks["Letter of Intent"].files] == ["45 Cedar LOI.pdf"]       # its own, deeper folder
    rr = picks["Rent Roll"].files
    assert [c.path.name for c in rr] == ["Rent Roll Oct 2026.xlsx"] and rr[0].picked and rr[0].why == "by its name"
    assert [c.path.name for c in picks["Property Tax Bill"].files] == ["2025 Property Tax Bill.pdf"]
    assert [c.path.name for c in picks["Insurance"].files] == ["Intact quote.pdf"] and picks["Insurance"].files[0].picked
    assert not picks["Leases and Tenant Forms"].files                    # "Release form" isn't a lease
    assert [c.path.name for c in picks["Property Photos"].files] == ["front.jpg"]
    assert not picks["Appraisal"].files
    chosen = [lpk.Pick(pk.doc, [c for c in pk.files if c.picked]) for pk in picks.values()]
    pkg = lpk.generate(f, chosen, "2026-11-11 First National Submission")
    assert pkg == f / lpk.PACKAGES / "2026-11-11 First National Submission"
    names = [x.name for x in sorted(pkg.iterdir())]
    assert names[0] == lpk.CHECKLIST_FILE and "01 Purchase and Sale Agreement" in names and "03 Rent Roll" in names
    assert "09 Appraisal" not in names                                  # nothing for it: only on the checklist
    assert (pkg / "03 Rent Roll/Rent Roll Oct 2026.xlsx").read_text() == "x"
    assert (f / "Underwriting/Due Diligence Files/Rent Roll Oct 2026.xlsx").exists()      # copied, not moved
    from openpyxl import load_workbook

    ws = load_workbook(pkg / lpk.CHECKLIST_FILE).active
    rows = {r[1]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    assert rows["Rent Roll"][2] == "Included" and rows["Appraisal"][2] == "Missing"
    assert len(rows) == len(lpk.CHECKLIST)
    again = lpk.generate(f, chosen, "2026-11-11 First National Submission")     # same name: a new folder
    assert again.name == "2026-11-11 First National Submission (2)"
    # the packages themselves aren't offered next time
    assert all("Lender Submission Package" not in c.rel for pk in lpk.candidates(f) for c in pk.files)
    pf.close()


def test_dialog_from_the_deal_folder_tab(app, tmp_path):
    from am_accounting.gui import deal_folder_tab as dft

    pf, d, f = _deal(tmp_path)
    tab = dft.DealFolderTab(pf, d)
    assert tab.btns["package"].text() == "Lender Package…"
    extra = _touch(tmp_path / "elsewhere" / "Net Worth Statement.pdf")

    def run(dlg):
        tops = {dlg.tree.topLevelItem(i).text(0).split("  ", 1)[1]: dlg.tree.topLevelItem(i)
                for i in range(dlg.tree.topLevelItemCount())}
        assert tops["Rent Roll"].checkState(0) == Qt.CheckState.Checked
        assert tops["Appraisal"].text(1) == "missing"
        tops["Property Photos"].child(0).setCheckState(0, Qt.CheckState.Unchecked)      # leave the photos out
        dlg.tree.setCurrentItem(tops["Borrower and Guarantor Information"])
        assert dlg.add_files([str(extra)]) == 1
        dlg.name.setText("Test Package")
        return dlg.generate() is not None

    pkg = tab.lender_package(run=run)
    assert pkg == f / lpk.PACKAGES / "Test Package"
    assert (pkg / "17 Borrower and Guarantor Information/Net Worth Statement.pdf").exists()
    assert not (pkg / "14 Property Photos").exists()
    assert (pkg / "01 Purchase and Sale Agreement/45 Cedar APS signed.pdf").exists()
    pf.close()
