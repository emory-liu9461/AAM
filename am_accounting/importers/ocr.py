"""OCR for scanned / image-only PDF statements.

Uses RapidOCR (ONNX models bundled with the package, runs fully offline) to read
each page rendered as an image, then rebuilds text lines from the word boxes so
the normal statement parser can read them.
"""

from __future__ import annotations

import statistics
from typing import Callable

_engine = None
unavailable_reason = ""


def ocr_available() -> bool:
    """True when the OCR engine can be loaded. On failure, ``unavailable_reason`` says why."""
    global unavailable_reason
    try:
        import pypdfium2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except Exception as e:  # noqa: BLE001
        import traceback

        unavailable_reason = "".join(traceback.format_exception(e))
        return False
    return True


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def _lines_from_boxes(result) -> list[str]:
    """Group OCR word boxes into text lines (top to bottom, left to right)."""
    items = []
    for box, text, _score in result or []:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), max(xs), text.strip()))
    if not items:
        return []
    height = statistics.median(i[1] for i in items) or 10
    items.sort(key=lambda i: i[0])
    rows: list[list] = []
    for it in items:
        if rows and abs(it[0] - rows[-1][0][0]) <= height * 0.55:
            rows[-1].append(it)
        else:
            rows.append([it])
    lines = []
    for row in rows:
        row.sort(key=lambda i: i[2])
        parts = []
        prev_right = None
        for _yc, _h, left, right, text in row:
            if prev_right is not None and left - prev_right > height * 1.5:
                parts.append("  ")  # a column gap
            parts.append(text)
            prev_right = right
        from .respace import respace

        lines.append(respace(" ".join(p for p in parts if p).replace("   ", "  ")))
    return lines


def ocr_pdf_text(data: bytes, *, dpi: int = 220, max_pages: int = 40,
                 progress: Callable[[int, int], None] | None = None) -> str:
    """Read the text of a PDF by OCR. Returns pages separated by blank lines."""
    import pypdfium2 as pdfium

    engine = _get_engine()
    pdf = pdfium.PdfDocument(data)
    try:
        n = min(len(pdf), max_pages)
        pages = []
        for i in range(n):
            if progress:
                progress(i + 1, n)
            image = pdf[i].render(scale=dpi / 72, grayscale=True).to_pil().convert("RGB")
            import numpy as np

            result, _ = engine(np.asarray(image))
            pages.append("\n".join(_lines_from_boxes(result)))
        return "\n\n".join(pages)
    finally:
        pdf.close()


def ocr_image_text(path) -> str:
    """Read the text of a photo or scanned image (JPG, PNG, TIFF…)."""
    import numpy as np
    from PIL import Image, ImageOps

    engine = _get_engine()
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")  # phone photos: honour rotation
        if max(im.size) > 3000:
            im.thumbnail((3000, 3000))
        result, _ = engine(np.asarray(im))
    return "\n".join(_lines_from_boxes(result))
