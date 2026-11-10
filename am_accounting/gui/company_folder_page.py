"""Asset Management → a company's Deal Folder: the same Deal Folder tab as in its deal, for the company picked.

A company that came from the Deal Pipeline uses its deal's folder. One added to the portfolio another way gets a
(closed) deal record the first time a folder is chosen for it, so it has a deal folder like the others.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import deals as dl
from .operations_tab import _CompanyPage


def deal_for(pf, holding) -> dl.Deal | None:
    return next((d for d in dl.deals(pf) if d.holding_id == holding.id), None)


class CompanyFolderPage(_CompanyPage):
    def __init__(self, view):
        super().__init__(view)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        row.addWidget(self.pick)
        row.addStretch(1)
        lay.addLayout(row)
        self.body = QVBoxLayout()
        lay.addLayout(self.body, 1)
        self.tab: QWidget | None = None

    def _clear(self) -> None:
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.tab = None

    def fill(self) -> None:
        from .deal_folder_tab import DealFolderTab

        self._clear()
        p = self.perf()
        if p is None:
            self.body.addWidget(QLabel("No company picked."))
            return
        d = deal_for(self.pf, p.holding)
        if d is None:                      # not from the pipeline: no deal record yet
            box = QWidget()
            bl = QVBoxLayout(box)
            msg = QLabel(f"<b>{p.holding.name}</b> has no deal folder yet. Choose where it goes — in your Deals folder "
                         "(Existing Deals), or a folder of its own — and it gets the standard folders (Underwriting and "
                         "Asset Management), with its accounting linked to Asset Management → Accounting.")
            msg.setWordWrap(True)
            bl.addWidget(msg)
            b = QPushButton("Choose a Folder for This Company…")
            b.setObjectName("primary")
            b.clicked.connect(self.choose)
            brow = QHBoxLayout()
            brow.addWidget(b)
            brow.addStretch(1)
            bl.addLayout(brow)
            bl.addStretch(1)
            self.body.addWidget(box)
            return
        self.tab = DealFolderTab(self.pf, d)
        self.body.addWidget(self.tab, 1)

    def deal(self) -> dl.Deal | None:
        p = self.perf()
        if p is None:
            return None
        d = deal_for(self.pf, p.holding)
        if d is None:                      # a closed deal for the company, linked to it
            h = p.holding
            d = dl.Deal(h.name, address=h.location or "", asset_type=h.sector or "Multifamily", stage="Closed",
                        closing_date=h.acquired or None, holding_id=h.id,
                        notes="Made for the company's deal folder")
            dl.save(self.pf, d)
        return d

    def choose(self, **kw):
        from .. import deal_folders as dfo
        from .deal_folder_tab import ask_where

        d = self.deal()
        if d is None:
            return None
        f = ask_where(self, self.pf, d, **kw)
        if f is not None:
            try:                            # a closed deal: Asset Management too, and its accounting linked
                dfo.on_closing(self.pf, d)
            except (OSError, ValueError):
                pass
        self.fill()
        return dfo.folder_of(self.pf, d) if f is not None else None
