"""1:1 vector PDF of the laser sheet (page size = sheet size, 1 mm = 72/25.4 pt).

Cut contours are red hairlines, engraving is blue - the same layers as the SVG/DXF.
"""

from __future__ import annotations

import io

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

from app.core.errors import ErrorCode, PuzzleError
from app.exporters.sheet import SheetGeometry

MM = 72.0 / 25.4
HAIRLINE_PT = 0.1 * MM
RED = Color(1, 0, 0)
BLUE = Color(0, 0, 1)


def draw_sheet(c: canvas.Canvas, sheet: SheetGeometry, x0: float = 0, y0: float = 0, scale: float = MM) -> None:
    """Draw sheet geometry with its origin at (x0, y0) points and `scale` points per mm."""
    c.setLineWidth(max(HAIRLINE_PT * scale / MM, 0.25))
    c.setLineJoin(1)
    c.setLineCap(1)
    c.setStrokeColor(RED)
    for piece in sheet.pieces:
        path = c.beginPath()
        for ring in piece.cut_rings:
            path.moveTo(x0 + ring[0][0] * scale, y0 + ring[0][1] * scale)
            for x, y in ring[1:]:
                path.lineTo(x0 + x * scale, y0 + y * scale)
            path.close()
        c.drawPath(path, stroke=1, fill=0)
    c.setStrokeColor(BLUE)
    for piece in sheet.pieces:
        for stroke in piece.engrave:
            path = c.beginPath()
            path.moveTo(x0 + stroke[0][0] * scale, y0 + stroke[0][1] * scale)
            for x, y in stroke[1:]:
                path.lineTo(x0 + x * scale, y0 + y * scale)
            c.drawPath(path, stroke=1, fill=0)


def export_sheet_pdf(sheet: SheetGeometry, title: str = "PuzzleCut AI laser sheet") -> bytes:
    try:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(sheet.width_mm * MM, sheet.height_mm * MM), pageCompression=1)
        c.setTitle(title)
        c.setAuthor("PuzzleCut AI")
        draw_sheet(c, sheet)
        c.showPage()
        c.save()
        return buf.getvalue()
    except Exception as exc:
        raise PuzzleError(ErrorCode.PDF_EXPORT_FAILED, "PDF generation failed.", repr(exc)) from exc
