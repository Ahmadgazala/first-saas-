"""SVG / DXF / PDF / guide / metadata exports, inspected after re-parsing the files."""

import io
import json
import re
import xml.etree.ElementTree as ET

import ezdxf
import numpy as np
import pytest
from pypdf import PdfReader
from shapely.geometry import Polygon

from app.exporters.assembly_guide import build_steps, export_assembly_guide
from app.exporters.dxf import export_dxf
from app.exporters.metadata import build_metadata, build_model_json
from app.exporters.pdf import MM, export_sheet_pdf
from app.exporters.render import render_preview
from app.exporters.sheet import build_sheet
from app.exporters.svg import export_svg
from app.geometry.nesting import nest
from app.geometry.slots import compensate_kerf
from tests.conftest import build_model

NS = {"svg": "http://www.w3.org/2000/svg"}
REQUIRED_METADATA = [
    "material", "thickness_mm", "kerf_mm", "clearance_mm", "sheet_width_mm", "sheet_height_mm", "piece_count",
    "object_width_mm", "object_height_mm", "object_depth_mm", "waste_percentage",
]


@pytest.fixture(scope="module")
def job():
    model = build_model("animal", 3.0, 12, "medium")
    tool = {p.id: compensate_kerf(p.polygon, model.params.kerf_mm) for p in model.pieces}
    layout = nest(tool, 600, 400, 5, 3)
    return model, layout, build_sheet(model, layout), tool


def parse_svg_rings(d: str):
    rings = []
    for sub in re.findall(r"M[^MZ]+Z?", d):
        pts = [(float(a), float(b)) for a, b in re.findall(r"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)", sub)]
        rings.append((pts, sub.endswith("Z")))
    return rings


def test_svg_structure_and_units(job):
    model, layout, sheet, _ = job
    root = ET.fromstring(export_svg(sheet))
    assert root.get("width") == "600mm" and root.get("height") == "400mm"
    assert root.get("viewBox") == "0 0 600 400"
    assert not root.findall(".//svg:image", NS), "no raster images allowed"
    assert all(el.get("transform") is None for el in root.iter())
    layers = {g.get("id"): g for g in root.findall("svg:g", NS)}
    assert set(layers) == {"CUT", "ENGRAVE"}
    assert layers["CUT"].get("stroke") == "#FF0000"
    assert len(layers["CUT"].findall("svg:path", NS)) == len(model.pieces)
    labelled = sum(1 for p in model.pieces if p.engrave)
    assert len(layers["ENGRAVE"].findall("svg:path", NS)) == labelled


def test_svg_cut_paths_match_toolpaths(job):
    model, layout, sheet, _ = job
    root = ET.fromstring(export_svg(sheet))
    placed = {pl.piece_id: pl.polygon for pl in layout.placements}
    for path in root.find("svg:g[@id='CUT']", NS).findall("svg:path", NS):
        pid = path.get("id").removesuffix("-cut")
        rings = parse_svg_rings(path.get("d"))
        assert all(closed for _, closed in rings), "every cut contour must be closed"
        # Undo the SVG y flip and rebuild the polygon: it must equal the placed tool path.
        coords = [[(x, 400 - y) for x, y in pts] for pts, _ in rings]
        poly = Polygon(coords[0], coords[1:])
        assert poly.is_valid
        # Files carry 3 decimals: every vertex within 1 µm of the tool path.
        assert poly.hausdorff_distance(placed[pid]) < 1e-3
        assert poly.area == pytest.approx(placed[pid].area, abs=placed[pid].length * 5e-4)
        for x, y in coords[0]:
            assert 5 - 1e-3 <= x <= 595 + 1e-3 and 5 - 1e-3 <= y <= 395 + 1e-3


def test_svg_has_no_duplicate_cut_lines(job):
    _, _, sheet, _ = job
    root = ET.fromstring(export_svg(sheet))
    segments = set()
    for path in root.find("svg:g[@id='CUT']", NS).findall("svg:path", NS):
        for pts, _ in parse_svg_rings(path.get("d")):
            ring = pts + [pts[0]]
            for a, b in zip(ring, ring[1:]):
                key = (min(a, b), max(a, b))
                assert key not in segments
                segments.add(key)


def test_dxf_is_valid_and_matches_geometry(job, tmp_path):
    model, layout, sheet, _ = job
    data = export_dxf(sheet)
    path = tmp_path / "sheet.dxf"
    path.write_bytes(data)
    doc = ezdxf.readfile(path)
    assert not doc.audit().has_errors
    assert doc.units == ezdxf.units.MM
    msp = doc.modelspace()
    cut = list(msp.query("LWPOLYLINE[layer=='CUT']"))
    engrave = list(msp.query("LWPOLYLINE[layer=='ENGRAVE']"))
    assert len(cut) == sheet.cut_ring_count()
    assert len(engrave) == sheet.engrave_path_count()
    assert all(e.closed for e in cut)
    assert {e.dxftype() for e in msp} == {"LWPOLYLINE"}
    total = sum(abs(Polygon([(p[0], p[1]) for p in e.get_points("xy")]).area) for e in cut)
    exteriors = sum(pl.polygon.area for pl in layout.placements)
    holes = sum(Polygon(r).area for pl in layout.placements for r in pl.polygon.interiors)
    assert total == pytest.approx(exteriors + 2 * holes, rel=1e-6)
    pts = np.array([p for e in cut for p in e.get_points("xy")])
    assert pts.min() >= 5 - 1e-6 and pts[:, 0].max() <= 595 + 1e-6 and pts[:, 1].max() <= 395 + 1e-6


def test_sheet_pdf_is_true_scale(job):
    _, _, sheet, _ = job
    reader = PdfReader(io.BytesIO(export_sheet_pdf(sheet)))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert float(box.width) == pytest.approx(600 * MM, abs=0.01)
    assert float(box.height) == pytest.approx(400 * MM, abs=0.01)


def test_assembly_guide(job):
    model, layout, sheet, _ = job
    data = export_assembly_guide(model, layout, sheet, "Plywood")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) >= 4
    text = "\n".join(p.extract_text() for p in reader.pages)
    for heading in ("Assembly Guide", "Parts Overview", "Piece Identification", "Exploded View", "Assembly Sequence"):
        assert heading in text
    assert "Connects to:" in text
    for pc in model.pieces:
        assert pc.id in text
    for j in model.joints:
        assert j.id in text
    images = sum(len(p.images) for p in reader.pages)
    assert images >= 3  # assembled, exploded and step figures


def test_assembly_steps_cover_every_piece(job):
    model = job[0]
    steps = build_steps(model)
    highlighted = set()
    for step in steps[:-1]:
        highlighted |= {it.piece.id for it in step.items if it.color == (37, 99, 235)}
    assert highlighted == {p.id for p in model.pieces}
    assert len(steps[-1].items) == len(model.pieces)


def test_metadata_contents(job):
    model, layout, _, _ = job
    meta = build_metadata(model, layout, "Plywood")
    for key in REQUIRED_METADATA:
        assert key in meta
    assert meta["piece_count"] == 12 and meta["thickness_mm"] == 3.0
    assert meta["kerf_mm"] == 0.15 and meta["clearance_mm"] == 0.1
    assert meta["slot_width_nominal_mm"] == pytest.approx(3.1)
    assert meta["slot_width_toolpath_mm"] == pytest.approx(2.95)
    assert meta["waste_percentage"] == layout.waste_percentage
    assert meta["object_depth_mm"] == model.object_size_mm[2]
    json.dumps(meta)


def test_model_json_matches_exported_pieces(job):
    model = job[0]
    data = build_model_json(model)
    assert len(data["pieces"]) == len(model.pieces)
    for entry, pc in zip(data["pieces"], model.pieces):
        poly = Polygon(entry["outer"], entry["holes"])
        assert poly.hausdorff_distance(pc.polygon) < 1e-3
        assert np.allclose(entry["transform"]["n"], pc.transform.normal)


def test_preview_render(job):
    img = render_preview(job[0], (400, 300))
    arr = np.asarray(img)
    assert img.size == (400, 300)
    assert (arr < 250).any(axis=2).mean() > 0.05  # something was drawn
