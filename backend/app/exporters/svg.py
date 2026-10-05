"""Laser-ready SVG export.

* Physical size in millimetres: width="600mm" height="400mm" viewBox="0 0 600 400",
  so 1 user unit = 1 mm. No transforms are used anywhere; coordinates are absolute.
* Layer "CUT": one closed path per piece (outer contour + holes, even-odd), red hairline.
* Layer "ENGRAVE": label strokes as open paths, blue hairline. Never cut.
* SVG's y axis points down, so sheet y is flipped once here: Y = height - y.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.core.errors import ErrorCode, PuzzleError
from app.exporters.sheet import SheetGeometry

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
CUT_COLOR = "#FF0000"
ENGRAVE_COLOR = "#0000FF"
STROKE_WIDTH = "0.1"


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _ring_d(ring, height: float) -> str:
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    head = f"M{_fmt(pts[0][0])} {_fmt(height - pts[0][1])}"
    body = "".join(f"L{_fmt(x)} {_fmt(height - y)}" for x, y in pts[1:])
    return head + body + "Z"


def _polyline_d(points, height: float) -> str:
    head = f"M{_fmt(points[0][0])} {_fmt(height - points[0][1])}"
    return head + "".join(f"L{_fmt(x)} {_fmt(height - y)}" for x, y in points[1:])


def export_svg(sheet: SheetGeometry, title: str = "PuzzleCut AI laser sheet") -> bytes:
    try:
        ET.register_namespace("", SVG_NS)
        ET.register_namespace("inkscape", INKSCAPE_NS)
        w, h = sheet.width_mm, sheet.height_mm
        root = ET.Element(
            f"{{{SVG_NS}}}svg",
            {
                "version": "1.1",
                "width": f"{_fmt(w)}mm",
                "height": f"{_fmt(h)}mm",
                "viewBox": f"0 0 {_fmt(w)} {_fmt(h)}",
            },
        )
        ET.SubElement(root, f"{{{SVG_NS}}}title").text = title
        cut = ET.SubElement(
            root,
            f"{{{SVG_NS}}}g",
            {
                "id": "CUT",
                f"{{{INKSCAPE_NS}}}groupmode": "layer",
                f"{{{INKSCAPE_NS}}}label": "CUT",
                "fill": "none",
                "stroke": CUT_COLOR,
                "stroke-width": STROKE_WIDTH,
            },
        )
        engrave = ET.SubElement(
            root,
            f"{{{SVG_NS}}}g",
            {
                "id": "ENGRAVE",
                f"{{{INKSCAPE_NS}}}groupmode": "layer",
                f"{{{INKSCAPE_NS}}}label": "ENGRAVE",
                "fill": "none",
                "stroke": ENGRAVE_COLOR,
                "stroke-width": STROKE_WIDTH,
                "stroke-linecap": "round",
                "stroke-linejoin": "round",
            },
        )
        for piece in sheet.pieces:
            d = "".join(_ring_d(r, h) for r in piece.cut_rings)
            ET.SubElement(cut, f"{{{SVG_NS}}}path", {"id": f"{piece.id}-cut", "d": d, "fill-rule": "evenodd"})
            if piece.engrave:
                d = "".join(_polyline_d(s, h) for s in piece.engrave if len(s) >= 2)
                ET.SubElement(engrave, f"{{{SVG_NS}}}path", {"id": f"{piece.id}-label", "d": d})
        if not len(engrave):
            root.remove(engrave)
        ET.indent(root)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    except Exception as exc:  # pragma: no cover - defensive, surfaced as a clean API error
        raise PuzzleError(ErrorCode.SVG_EXPORT_FAILED, "SVG generation failed.", repr(exc)) from exc
