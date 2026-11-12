"""Charts for the Net Worth page: lines over time (net worth, assets, liabilities, cash) and stacked areas (assets by
type)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


COLOURS = ["#2a6fdb", "#1b7f3b", "#b3261e", "#c77700", "#7b3fbf", "#00838f", "#8d6e63", "#5c6bc0", "#9e9d24",
           "#ad1457"]


def _short(cents: int) -> str:
    v = cents / 100
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.1f}{suf}".replace(".0", "")
    return f"${v:,.0f}"


def _label(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x:%b} {x.day}, {x:%y}"


class TimeChart(QWidget):
    """Series over dates: lines (``stacked=False``) or stacked areas (``stacked=True``)."""

    def __init__(self, title: str, *, stacked: bool = False, empty: str = ""):
        super().__init__()
        self.title, self.stacked = title, stacked
        self.empty = empty or "Nothing recorded yet"
        self.dates: list[str] = []
        self.series: list[tuple[str, list[int]]] = []    # (name, a value per date)
        self.setMinimumHeight(190)

    def set_data(self, dates: list[str], series: list[tuple[str, list[int]]]) -> None:
        self.dates, self.series = dates, [(n, v) for n, v in series if any(v)] or series[:1]
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text, mid = self.palette().text().color(), self.palette().mid().color()
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.setPen(text)
        p.drawText(4, 14, self.title)
        f.setBold(False)
        p.setFont(f)
        if not self.dates or not self.series:
            p.setPen(self.palette().placeholderText().color())
            p.drawText(4, 40, self.empty)
            return
        # legend
        x = 4
        for i, (name, vals) in enumerate(self.series):
            c = QColor(COLOURS[i % len(COLOURS)])
            label = f"{name} {_short(vals[-1])}"
            w = p.fontMetrics().horizontalAdvance(label) + 26
            if x + w > self.width() - 4:
                break
            p.fillRect(QRectF(x, 22, 10, 10), c)
            p.setPen(text)
            p.drawText(int(x + 14), 31, label)
            x += w
        top, bottom, left, right = 44, self.height() - 22, 58, self.width() - 12
        if self.stacked:
            tops = [sum(v[k] for _n, v in self.series) for k in range(len(self.dates))]
            hi, lo = max(max(tops), 0), 0
        else:
            vals = [v for _n, vs in self.series for v in vs]
            hi, lo = max(max(vals), 0), min(min(vals), 0)
        span = (hi - lo) or 1

        def y(v):
            return bottom - (bottom - top) * (v - lo) / span

        n = len(self.dates)

        def xs(k):
            return left + (right - left) * (k / (n - 1) if n > 1 else 0.5)

        # grid and axis labels
        p.setPen(QPen(mid, 1, Qt.PenStyle.DotLine))
        for g in range(5):
            v = lo + span * g / 4
            yy = y(v)
            p.drawLine(QPointF(left, yy), QPointF(right, yy))
            p.setPen(text)
            p.drawText(QRectF(0, yy - 8, left - 6, 16), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       _short(round(v)))
            p.setPen(QPen(mid, 1, Qt.PenStyle.DotLine))
        p.setPen(text)
        step = max(1, n // max(1, int((right - left) // 90)))
        for k in range(0, n, step):
            lx = min(max(xs(k) - 45, 0), self.width() - 92)          # kept inside the chart
            p.drawText(QRectF(lx, bottom + 4, 90, 16), int(Qt.AlignmentFlag.AlignHCenter), _label(self.dates[k]))
        if self.stacked:
            base = [0] * n
            for i, (_name, vals) in enumerate(self.series):
                c = QColor(COLOURS[i % len(COLOURS)])
                upper = [base[k] + vals[k] for k in range(n)]
                if n == 1:
                    p.fillRect(QRectF(xs(0) - 18, y(upper[0]), 36, y(base[0]) - y(upper[0])), c)
                else:
                    path = QPainterPath(QPointF(xs(0), y(upper[0])))
                    for k in range(1, n):
                        path.lineTo(xs(k), y(upper[k]))
                    for k in range(n - 1, -1, -1):
                        path.lineTo(xs(k), y(base[k]))
                    path.closeSubpath()
                    c.setAlpha(200)
                    p.fillPath(path, c)
                base = upper
            return
        for i, (_name, vals) in enumerate(self.series):
            c = QColor(COLOURS[i % len(COLOURS)])
            p.setPen(QPen(c, 2))
            for k in range(1, n):
                p.drawLine(QPointF(xs(k - 1), y(vals[k - 1])), QPointF(xs(k), y(vals[k])))
            p.setBrush(c)
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(n):
                p.drawEllipse(QPointF(xs(k), y(vals[k])), 3, 3)
