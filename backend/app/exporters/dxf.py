"""DXF export with ezdxf (R2010, millimetres).

Layers: CUT (red, ACI 1) with one closed LWPOLYLINE per contour, ENGRAVE (blue, ACI 5) with
open LWPOLYLINEs for labels. The written document is re-read and audited before returning.
"""

from __future__ import annotations

import io

import ezdxf
from ezdxf import units

from app.core.errors import ErrorCode, PuzzleError
from app.exporters.sheet import SheetGeometry


def export_dxf(sheet: SheetGeometry) -> bytes:
    try:
        doc = ezdxf.new("R2010", setup=False)
        doc.units = units.MM
        doc.header["$MEASUREMENT"] = 1  # metric
        doc.header["$EXTMIN"] = (0, 0, 0)
        doc.header["$EXTMAX"] = (sheet.width_mm, sheet.height_mm, 0)
        doc.layers.add("CUT", color=1)
        doc.layers.add("ENGRAVE", color=5)
        msp = doc.modelspace()
        for piece in sheet.pieces:
            for ring in piece.cut_rings:
                pts = ring[:-1] if ring[0] == ring[-1] else ring
                msp.add_lwpolyline(pts, format="xy", close=True, dxfattribs={"layer": "CUT"})
            for stroke in piece.engrave:
                if len(stroke) >= 2:
                    msp.add_lwpolyline(stroke, format="xy", close=False, dxfattribs={"layer": "ENGRAVE"})
        stream = io.StringIO()
        doc.write(stream)
        data = stream.getvalue().encode("utf-8")
    except Exception as exc:
        raise PuzzleError(ErrorCode.DXF_EXPORT_FAILED, "DXF generation failed.", repr(exc)) from exc
    verify_dxf(data, expected_cut=sheet.cut_ring_count(), expected_engrave=sheet.engrave_path_count())
    return data


def verify_dxf(data: bytes, expected_cut: int, expected_engrave: int) -> None:
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8")))
        auditor = doc.audit()
    except Exception as exc:
        raise PuzzleError(ErrorCode.DXF_EXPORT_FAILED, "DXF generation failed.", f"re-read failed: {exc!r}") from exc
    if auditor.has_errors:
        raise PuzzleError(ErrorCode.DXF_EXPORT_FAILED, "DXF generation failed.", f"audit errors: {auditor.errors}")
    msp = doc.modelspace()
    cut = [e for e in msp.query("LWPOLYLINE[layer=='CUT']")]
    engrave = [e for e in msp.query("LWPOLYLINE[layer=='ENGRAVE']")]
    if len(cut) != expected_cut or len(engrave) != expected_engrave:
        raise PuzzleError(
            ErrorCode.DXF_EXPORT_FAILED,
            "DXF generation failed.",
            f"entity count mismatch cut={len(cut)}/{expected_cut} engrave={len(engrave)}/{expected_engrave}",
        )
    if any(not e.closed for e in cut):
        raise PuzzleError(ErrorCode.DXF_EXPORT_FAILED, "DXF generation failed.", "open cut contour")
