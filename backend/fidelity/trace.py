"""Pipeline trace / diagnostics for one input (image or 3D model).

Runs the production functions stage by stage and records what each stage produced, so geometry
problems can be inspected without relying on the rendered preview:

  input image -> processed mask -> 3D body (mesh stats: vertices, faces, bounds, dimensions,
  watertight) -> what the kit generator receives -> structural sections (every rib vs the real
  body cross-section on the same plane) -> parts -> slots -> validation -> nesting -> SVG

    .venv/bin/python -m fidelity.trace --image path/to/photo.jpg [--style kit] [--out dir]
    .venv/bin/python -m fidelity.trace --model path/to/model.stl
    .venv/bin/python -m fidelity.trace --gt quadruped        (procedural test mesh)

Writes <out>/trace.json, <out>/mask.png, <out>/mesh.ply (if any), <out>/laser_sheet.svg.
trace.json also records which body drove the kit (generator_input.kit_volume: per-rib source
mesh-section / template, limb depths), every cross piece vs the real section on its plane, slot
positions, validation checks, nesting and the measured kerf offset of the tool paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import warnings
from pathlib import Path

import numpy as np

from fidelity.run import DEFAULTS, clean_for_json, fresh_copy, params_for
from fidelity import metrics as M

warnings.filterwarnings("ignore")
logging.getLogger("app").setLevel(logging.ERROR)


def mesh_stats(mesh) -> dict:
    lo, hi = mesh.bounds
    return {"vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces)),
            "watertight": bool(mesh.is_watertight), "bounds_mm": [lo.round(2).tolist(), hi.round(2).tolist()],
            "dimensions_mm": (hi - lo).round(2).tolist(),
            "volume_mm3": float(mesh.volume) if mesh.is_volume else None}


def rib_vs_section(model, volume) -> list[dict]:
    """Every cross piece next to the real body section on the same plane (u = z, v = t).

    `width_ratio_piece_to_section` compares lateral extents within the piece's own t range
    (a rib that stops at the backbone is not penalised for the body above it);
    `outside_body_mm2` / `max_protrusion_mm` measure material that pokes out of the body."""
    from shapely.geometry import Point, box

    from app.geometry.normalize import polygon_parts

    out = []
    T = model.params.thickness_mm
    for pc in model.pieces:
        if pc.frame is None:
            continue
        minx, miny, maxx, maxy = pc.polygon.bounds
        sec = volume.cross_section(pc.frame, miny - 200, maxy + 200, T) if volume is not None else None
        parts = polygon_parts(sec) if sec is not None else []
        sec_poly = max(parts, key=lambda p: p.area) if parts else None
        entry = {"id": pc.id, "role": pc.role.value, "station_xy": [round(pc.frame.cx, 2), round(pc.frame.cy, 2)],
                 "piece_u_extent_mm": round(maxx - minx, 2), "piece_t_extent_mm": round(maxy - miny, 2)}
        if sec_poly is not None:
            sx0, sy0, sx1, sy1 = sec_poly.bounds
            env = M.as_area(pc.polygon).convex_hull
            band = M.as_area(M.as_area(sec).intersection(box(-1e4, miny, 1e4, maxy)))
            near = [q for q in M.parts(band) if q.intersects(env)]
            bx0 = min((q.bounds[0] for q in near), default=sx0)
            bx1 = max((q.bounds[2] for q in near), default=sx1)
            body = M.as_area(sec)
            outside = M.as_area(pc.polygon).difference(body)
            prot = max((body.boundary.distance(Point(xy)) for xy in pc.polygon.exterior.coords
                        if not body.contains(Point(xy))), default=0.0)
            entry.update({"section_u_extent_mm": round(sx1 - sx0, 2), "section_t_extent_mm": round(sy1 - sy0, 2),
                          "section_u_extent_in_piece_t_range_mm": round(bx1 - bx0, 2),
                          "width_ratio_piece_to_section": round((maxx - minx) / max(bx1 - bx0, 1e-6), 3),
                          "hull_iou_with_section": round(M.iou(env, sec_poly), 3),
                          "outside_body_mm2": round(outside.area, 1), "max_protrusion_mm": round(prot, 2)})
        out.append(entry)
    return out


def trace(image: bytes | None = None, mesh=None, style: str = "kit", out: Path | None = None,
          shape_source: str = "auto", depth_only: bool = False) -> dict:
    """`depth_only`: reproduce the pre-fix hand-off (the kit generator gets the mesh's outline and
    depth but not the mesh) for A/B comparisons on the same input."""
    from app.geometry.types import Style
    from app.geometry.volume import MeshVolume
    from app.services.pipeline import decode_mask, extract_silhouette, generate_puzzle
    from app.validators.geometry_validator import validate_model

    rep: dict = {"style": style}
    out = out or Path("trace_out")
    out.mkdir(parents=True, exist_ok=True)
    mask = None
    if image is not None:
        rep["input"] = {"bytes": len(image), "sha256": hashlib.sha256(image).hexdigest()[:16]}
        sil = extract_silhouette(image)
        mask = decode_mask(sil.mask_png)
        (out / "mask.png").write_bytes(sil.mask_png)
        rep["processed_image"] = {"segmentation": sil.method, "confidence": sil.confidence,
                                  "mask_shape": list(mask.shape), "foreground_fraction": float((mask > 0).mean()),
                                  "suitability": {k: (sil.suitability or {}).get(k) for k in ("suitable", "overall_confidence", "blocking")}}
        if mesh is None and shape_source != "silhouette":
            from fidelity.cases import Case
            from fidelity.run import reconstruction

            rec = reconstruction(Case("trace", "photo", image=image), image, mask)
            rep["reconstruction"] = {k: v for k, v in rec.items() if k != "mesh"}
            if rec.get("available") and rec.get("gate") == "pass":
                mesh = rec["mesh"]
    elif mesh is not None:
        from app.geometry.volume import silhouette_mask_png

        mask = silhouette_mask_png(MeshVolume(fresh_copy(mesh)))
    volume = None
    if mesh is not None:
        rep["mesh_stats"] = mesh_stats(mesh)  # ("mesh" keys are dropped by clean_for_json)
        mesh.export(out / "mesh.ply")
        volume = MeshVolume(mesh)
        rep["volume"] = {"kind": "mesh", "silhouette_area_mm2": round(volume.silhouette.area, 1),
                         "max_half_depth_mm": round(float(volume.depth.values.max()), 2)}
    params = params_for(Style(style), "trace")
    rep["depth_only_handoff"] = depth_only
    if depth_only:
        from unittest import mock

        from app.geometry.kit.builder import KitGenerator

        class DepthOnly(KitGenerator):
            def generate(self, silhouette, depth=None, skeleton=None, volume=None):
                return super().generate(silhouette, depth=depth, skeleton=skeleton)

        with mock.patch("app.services.pipeline.KitGenerator", DepthOnly):
            res = generate_puzzle(mask, params, "Plywood", outputs={"dxf"}, mesh=mesh)
    else:
        res = generate_puzzle(mask, params, "Plywood", outputs={"dxf"}, mesh=mesh)
    model = res.model
    rep["generator_input"] = {"shape_source": model.analysis.get("shape_source"),
                              "kit_volume": model.analysis.get("kit_volume")}
    rep["sections"] = rib_vs_section(model, volume)
    if rep["sections"] and volume is not None:
        def med(key, role=None):
            v = [s[key] for s in rep["sections"] if key in s and (role is None or s["role"] == role)]
            return round(float(np.median(v)), 3) if v else None

        rep["sections_summary"] = {"cross_pieces": len(rep["sections"]),
                                   "median_hull_iou_with_mesh_section": med("hull_iou_with_section"),
                                   "rib_median_width_ratio": med("width_ratio_piece_to_section", "rib"),
                                   "rib_median_hull_iou": med("hull_iou_with_section", "rib"),
                                   "vertebra_median_width_ratio": med("width_ratio_piece_to_section", "vertebra"),
                                   "max_protrusion_mm": max((s.get("max_protrusion_mm", 0.0) for s in rep["sections"]), default=0.0),
                                   "outside_body_mm2_total": round(sum(s.get("outside_body_mm2", 0.0) for s in rep["sections"]), 1)}
    roles: dict[str, int] = {}
    for pc in model.pieces:
        roles[pc.role.value] = roles.get(pc.role.value, 0) + 1
    rep["parts"] = {"count": len(model.pieces), "roles": roles,
                    "pieces": [{"id": pc.id, "role": pc.role.value, "kind": pc.kind.value,
                                "bbox_mm": [round(v, 2) for v in pc.polygon.bounds], "area_mm2": round(pc.polygon.area, 1),
                                "holes": len(pc.polygon.interiors), "slots": len(pc.slots)} for pc in model.pieces]}
    rep["slots"] = [{"joint": j.id, "plate": j.profile_id, "cross": j.cross_id,
                     "xyz_mm": [round(j.x_mm, 2), round(j.y_mm, 2), round(j.z_mm, 2)],
                     "engagement_mm": round(j.engagement_mm, 2)} for j in model.joints]
    v = validate_model(model)
    rep["validation"] = {"ok": v.ok, "errors": v.errors[:20], "checks": v.checks}
    lay = res.layout
    rep["nesting"] = {"sheet_mm": [lay.sheet_width_mm, lay.sheet_height_mm], "placed": len(lay.placements),
                      "pieces": len(model.pieces), "used_area_mm2": lay.used_area_mm2,
                      "waste_percentage": lay.waste_percentage}
    # Kerf: every placed tool path is the piece outline offset outwards by kerf/2
    # (area gain / perimeter ~= offset for small offsets), and lies on the sheet.
    by_id = {pc.id: pc for pc in model.pieces}
    offs = [(pl.polygon.area - by_id[pl.piece_id].polygon.area) / by_id[pl.piece_id].polygon.length
            for pl in lay.placements if pl.piece_id in by_id]
    W, H = lay.sheet_width_mm, lay.sheet_height_mm
    rep["kerf"] = {"kerf_mm": params.kerf_mm, "expected_outward_offset_mm": params.kerf_mm / 2,
                   "measured_offset_mm_median": round(float(np.median(offs)), 4) if offs else None,
                   "measured_offset_mm_range": [round(float(min(offs)), 4), round(float(max(offs)), 4)] if offs else None}
    rep["nesting"]["all_on_sheet"] = all(0 <= pl.polygon.bounds[0] and 0 <= pl.polygon.bounds[1]
                                         and pl.polygon.bounds[2] <= W and pl.polygon.bounds[3] <= H
                                         for pl in lay.placements)
    svg = res.files["svg"]
    (out / "laser_sheet.svg").write_bytes(svg)
    rep["svg"] = {"bytes": len(svg), "sha256": hashlib.sha256(svg).hexdigest()[:16],
                  "cut_paths": svg.count(b'-cut"'), "engrave_paths": svg.count(b'-label"')}
    rep["warnings"] = [w for w in model.warnings if "label" not in w]
    (out / "trace.json").write_text(json.dumps(clean_for_json(rep), indent=1))
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--model")
    ap.add_argument("--gt", choices=["quadruped", "dino", "bird"])
    ap.add_argument("--style", default="kit")
    ap.add_argument("--shape-source", default="auto")
    ap.add_argument("--out", default="trace_out")
    ap.add_argument("--depth-only", action="store_true", help="pre-fix hand-off (A/B): no mesh sections")
    a = ap.parse_args()
    mesh = image = None
    if a.image:
        image = Path(a.image).read_bytes()
    elif a.model:
        from app.geometry.volume import load_mesh_bytes, orient_uploaded_model, prepare_mesh, remesh

        ext = Path(a.model).suffix.lstrip(".").lower()
        clean = remesh(load_mesh_bytes(Path(a.model).read_bytes(), ext))
        mesh = prepare_mesh(clean, orient_uploaded_model(clean, ext), DEFAULTS["target_size_mm"])
    elif a.gt:
        from app.geometry.volume import prepare_mesh
        from fidelity.cases import bird_mesh, dino_mesh
        from tests.meshes import quadruped

        from app.geometry.volume import remesh

        src = {"quadruped": quadruped, "dino": dino_mesh, "bird": bird_mesh}[a.gt]()
        # production upload path (already in the canonical frame: no orientation step)
        mesh = prepare_mesh(remesh(src), np.eye(3), DEFAULTS["target_size_mm"])
    rep = trace(image=image, mesh=mesh, style=a.style, out=Path(a.out), shape_source=a.shape_source,
                depth_only=a.depth_only)
    print(json.dumps({k: rep[k] for k in rep if k not in ("sections", "slots", "parts", "generator_input")}, indent=1,
                     default=str))
    kv = (rep.get("generator_input") or {}).get("kit_volume") or {}
    print("mesh usage:", json.dumps({"kind": kv.get("kind"), "rib_sources": kv.get("rib_sources"),
                                     "limb_groups": kv.get("limb_groups")}, default=str))
    print("sections:", json.dumps(rep.get("sections_summary"), default=str))
    print("parts:", rep["parts"]["count"], rep["parts"]["roles"])


if __name__ == "__main__":
    main()
