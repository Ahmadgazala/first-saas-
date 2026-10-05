"""metadata.json and model.json (the 3D preview's data, taken from the exported geometry)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.exporters.render import explode_offset, insertion_vector
from app.geometry.nesting import Layout
from app.geometry.slots import toolpath_slot_width
from app.geometry.types import PieceKind, PuzzleModel

GENERATOR_VERSION = "2.0.0"


def _r(v: float, nd: int = 3) -> float:
    return round(float(v), nd)


def connections(model: PuzzleModel) -> dict[str, list[str]]:
    """Piece ID -> IDs of the pieces it interlocks with (via any joint)."""
    out: dict[str, set[str]] = {pc.id: set() for pc in model.pieces}
    for j in model.joints:
        out[j.profile_id].add(j.cross_id)
        out[j.cross_id].add(j.profile_id)
    for conn in model.analysis.get("connectors", []):  # bowtie keys join coplanar plates
        for host in conn["pieces"]:
            if host in out and conn["id"] in out:
                out[host].add(conn["id"])
                out[conn["id"]].add(host)
    return {k: sorted(v) for k, v in out.items()}


def piece_summaries(model: PuzzleModel, layout: Layout | None = None) -> list[dict]:
    conn = connections(model)
    placements = {pl.piece_id: pl for pl in layout.placements} if layout else {}
    out = []
    for pc in model.pieces:
        minx, miny, maxx, maxy = pc.polygon.bounds
        entry = {
            "id": pc.id,
            "label": pc.label or pc.id,
            "kind": pc.kind.value,
            "role": pc.role.value,
            "region": pc.region,
            "width_mm": _r(maxx - minx, 2),
            "height_mm": _r(maxy - miny, 2),
            "area_mm2": _r(pc.polygon.area, 2),
            "z_mm": _r(pc.position_mm) if pc.kind == PieceKind.PROFILE else None,
            "tilt_deg": _r(pc.frame.angle_deg, 1) if pc.frame is not None else None,
            "slots": len(pc.slots),
            "connected_to": conn[pc.id],
            "labelled": bool(pc.engrave),
        }
        pl = placements.get(pc.id)
        if pl is not None:
            bx0, by0, bx1, by1 = pl.polygon.bounds
            entry["sheet"] = {
                "rotation_deg": pl.rotation_deg,
                "bbox_mm": [_r(bx0, 2), _r(by0, 2), _r(bx1, 2), _r(by1, 2)],
            }
        out.append(entry)
    return out


def build_metadata(
    model: PuzzleModel,
    layout: Layout,
    material: str,
    segmentation: dict | None = None,
    validation_checks: dict | None = None,
    quality: dict | None = None,
) -> dict:
    p = model.params
    w, h, d = model.object_size_mm
    roles: dict[str, int] = {}
    for pc in model.pieces:
        roles[pc.role.value] = roles.get(pc.role.value, 0) + 1
    return {
        "generator": "PuzzleCut AI",
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "material": material,
        "thickness_mm": p.thickness_mm,
        "kerf_mm": p.kerf_mm,
        "clearance_mm": p.clearance_mm,
        "slot_width_nominal_mm": p.slot_width_mm,
        "slot_width_toolpath_mm": _r(toolpath_slot_width(p.thickness_mm, p.clearance_mm, p.kerf_mm), 4),
        "kerf_compensation": "outward offset of kerf/2 on every cut contour (slots narrow by kerf in the file)",
        "sheet_width_mm": layout.sheet_width_mm,
        "sheet_height_mm": layout.sheet_height_mm,
        "sheet_margin_mm": p.sheet_margin_mm,
        "part_spacing_mm": p.part_spacing_mm,
        "piece_count": len(model.pieces),
        "profile_rib_count": sum(pc.kind == PieceKind.PROFILE for pc in model.pieces),
        "cross_rib_count": sum(pc.kind == PieceKind.CROSS for pc in model.pieces),
        "piece_roles": roles,
        "joint_count": len(model.joints),
        "object_width_mm": w,
        "object_height_mm": h,
        "object_depth_mm": d,
        "puzzle_size_mm": p.target_size_mm,
        "complexity": p.complexity.value,
        "orientation": model.orientation.value,
        "slide_direction": model.slide.value,
        "labels": p.labels,
        "used_area_mm2": layout.used_area_mm2,
        "waste_percentage": layout.waste_percentage,
        "structure": model.analysis,
        "quality": quality or {},
        "pieces": piece_summaries(model, layout),
        "joints": [
            {"id": j.id, "profile": j.profile_id, "cross": j.cross_id, "engagement_mm": j.engagement_mm,
             "load_bearing": j.load_bearing}
            for j in model.joints
        ],
        "connectors": model.analysis.get("connectors", []),
        "warnings": model.warnings,
        "segmentation": segmentation or {},
        "validation": {"passed": True, "checks": validation_checks or {}},
    }


def build_model_json(model: PuzzleModel, layout: Layout | None = None) -> dict:
    """Geometry for the Three.js viewer: each piece's exported nominal outline (with slots)
    plus its 3D placement. Nothing here is generated separately from the cut files."""
    summaries = {s["id"]: s for s in piece_summaries(model, layout)}
    pieces = []
    for pc in model.pieces:
        t = pc.transform
        info = summaries[pc.id]
        insert = insertion_vector(pc, model) if pc.kind == PieceKind.CROSS else None
        pieces.append(
            {
                "id": pc.id,
                "label": info["label"],
                "kind": pc.kind.value,
                "role": pc.role.value,
                "region": pc.region,
                "style": pc.style or None,
                "outer": [[_r(x), _r(y)] for x, y in pc.polygon.exterior.coords[:-1]],
                "holes": [[[_r(x), _r(y)] for x, y in ring.coords[:-1]] for ring in pc.polygon.interiors],
                "engrave": [[[_r(x), _r(y)] for x, y in s] for s in pc.engrave],
                "transform": {
                    "origin": [_r(v) for v in t.origin],
                    "u": [_r(v, 6) for v in t.u_axis],
                    "v": [_r(v, 6) for v in t.v_axis],
                    "n": [_r(v, 6) for v in t.normal],
                },
                "explode_offset": [_r(v) for v in explode_offset(pc, model)],
                "insertion": [_r(v, 6) for v in insert] if insert is not None else None,
                "width_mm": info["width_mm"],
                "height_mm": info["height_mm"],
                "area_mm2": info["area_mm2"],
                "slots": len(pc.slots),
                "connected_to": info["connected_to"],
                "sheet": info.get("sheet"),
            }
        )
    w, h, d = model.object_size_mm
    return {
        "thickness_mm": model.params.thickness_mm,
        "object_size_mm": {"width": w, "height": h, "depth": d},
        "sheet_mm": {"width": layout.sheet_width_mm, "height": layout.sheet_height_mm} if layout else None,
        "pieces": pieces,
        "joints": [
            {
                "id": j.id,
                "profile": j.profile_id,
                "cross": j.cross_id,
                "split_mm": _r(j.t_split),
                "engagement_mm": j.engagement_mm,
                "load_bearing": j.load_bearing,
            }
            for j in model.joints
        ],
    }
