"""Phase 2 fidelity harness runner.

For every case it runs the production pipeline functions exactly as a job would
(app.services.pipeline.extract_silhouette / generate_puzzle, the reconstruction gates of
JobService._body_mesh, the 3D-upload preparation of JobService._upload_model) with the web
app's default settings, and measures fidelity at every stage. Results are written per case
and per mode; nothing in app/ is modified (generators are observed through fidelity.instrument).

    .venv/bin/python -m fidelity.run [--only name1,name2] [--res 1.0] [--out fidelity/reports/phase2]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import time
import traceback
import warnings
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.types import Complexity, Orientation, PuzzleParameters, Style
from fidelity import assembly as A
from fidelity import metrics as M
from fidelity.cases import BACKEND, Case, all_cases
from fidelity.instrument import ENGINE_LOG, RECORDERS, InstrumentedKit, instrument_engine
from fidelity.stages import traced_normalize

warnings.filterwarnings("ignore")
logging.getLogger("app").setLevel(logging.ERROR)
CACHE = BACKEND / "fidelity" / "cache"

# Web-app defaults (frontend/src/lib/settings.ts DEFAULT_SETTINGS): medium size = 250 mm.
DEFAULTS = dict(thickness_mm=3.0, kerf_mm=0.15, clearance_mm=0.10, target_size_mm=250.0, piece_count=30,
                complexity=Complexity.ADVANCED, orientation=Orientation.AUTO, labels=True,
                sheet_width_mm=600.0, sheet_height_mm=400.0, kit_frame=True)
ENVELOPE_MM = 6.0
# Generation styles measured per case: (Style, label). `--styles slice` measures only the solid-slice style
# (with the web-app default of 40 pieces); the default keeps the Phase 2 pair.
STYLE_SETS = {"kit,solid": ((Style.KIT, "kit"), (Style.SOLID, "solid")), "slice": ((Style.SLICE, "slice"),),
              "all": ((Style.SLICE, "slice"), (Style.KIT, "kit"), (Style.SOLID, "solid"))}
STYLES = STYLE_SETS["kit,solid"]
SLICE_PIECES = 40


def params_for(style: Style, title: str, **kw) -> PuzzleParameters:
    base = {**DEFAULTS, "piece_count": SLICE_PIECES} if style == Style.SLICE else DEFAULTS
    return PuzzleParameters(style=style, title=title, **{**base, **kw})


# ------------------------------------------------------------------------------ inputs


def photo_bytes(case: Case) -> tuple[bytes, np.ndarray | None]:
    """Image bytes fed to the pipeline + the ground-truth mask (image pixels, y down) if any."""
    from tests.fixtures import encode, mask_to_photo

    if case.kind == "fixture":
        return encode(mask_to_photo(case.gt_mask), "PNG"), case.gt_mask
    if case.kind == "gtmesh":
        mask = gt_side_mask(case)
        return encode(mask_to_photo(mask), "PNG"), mask
    return case.image, None


def gt_prepared(case: Case):
    from app.geometry.volume import prepare_mesh

    return prepare_mesh(fresh_copy(case.gt_mesh), np.eye(3), DEFAULTS["target_size_mm"])


def gt_side_mask(case: Case) -> np.ndarray:
    from app.geometry.volume import MeshVolume, silhouette_mask_png

    return silhouette_mask_png(MeshVolume(gt_prepared(case)))


def px_geometry(mask_img: np.ndarray, like: np.ndarray):
    """Mask (image rows, y down) resized to the segmentation mask's shape, as pixel geometry."""
    from app.geometry.normalize import mask_to_geometry

    m = mask_img
    if m.shape != like.shape:
        m = cv2.resize(m, (like.shape[1], like.shape[0]), interpolation=cv2.INTER_NEAREST)
    return mask_to_geometry(m)


# ------------------------------------------------------------------------------ meshes


def fresh_copy(mesh):
    """Copy without app.geometry.volume's cached shell split (`metadata['_bodies']`), which
    trimesh copies along and which would be stale after a transform (see report: this stale
    cache also affects production's horizontal-orientation mesh slicing)."""
    m = mesh.copy()
    m.metadata.pop("_bodies", None)
    return m


def prepare_matrix(mesh, rotation: np.ndarray, size: float) -> np.ndarray:
    """4x4 transform applied by app.geometry.volume.prepare_mesh (mirrored, verified below)."""
    m = mesh.copy()
    R = np.eye(4)
    R[:3, :3] = rotation
    m.apply_transform(R)
    lo, hi = m.bounds
    s = size / float(max(hi[0] - lo[0], hi[1] - lo[1]))
    S = np.diag([s, s, s, 1.0])
    m.apply_transform(S)
    lo, hi = m.bounds
    zc = float(m.center_mass[2]) if m.is_volume else float((lo[2] + hi[2]) / 2.0)
    T = np.eye(4)
    T[:3, 3] = [-(lo[0] + hi[0]) / 2.0, -lo[1], -zc]
    return T @ S @ R


def upload_path(case: Case):
    """Production 3D-upload path (JobService._upload_model + _body_mesh), OBJ = y-up.
    Returns (generator mesh, GT mesh in the same frame, the remeshed mesh in that frame)."""
    from app.geometry.volume import load_mesh_bytes, orient_uploaded_model, prepare_mesh, remesh

    data = case.gt_mesh.export(file_type="obj")
    data = data.encode() if isinstance(data, str) else data
    loaded = load_mesh_bytes(data, "obj")
    clean = remesh(loaded)
    rot = orient_uploaded_model(clean, "obj")
    M1 = prepare_matrix(clean, rot, 250.0)
    oriented = prepare_mesh(clean, rot, 250.0)
    assert np.allclose(clean.copy().apply_transform(M1).vertices, oriented.vertices, atol=1e-6)
    size = DEFAULTS["target_size_mm"]
    M2 = prepare_matrix(oriented, np.eye(3), size)
    gen = prepare_mesh(oriented, np.eye(3), size)
    assert np.allclose(oriented.copy().apply_transform(M2).vertices, gen.vertices, atol=1e-6)
    gt = fresh_copy(loaded)
    gt.apply_transform(M2 @ M1)
    return gen, gt


def reconstruction(case: Case, image: bytes, mask: np.ndarray) -> dict:
    """AI 3D exactly like JobService._body_mesh (shape_source=auto), with a local cache."""
    from app.reconstruction.service import (
        MIN_ALIGNMENT_IOU,
        MIN_DEPTH_RATIO,
        canonical_from_reconstruction,
        depth_ratio,
        get_reconstructor,
        mesh_from_bytes,
        mesh_to_bytes,
    )
    from app.vision.image_io import load_image, resize_max_side

    import hashlib

    out: dict = {}
    key = CACHE / f"{hashlib.sha256(image).hexdigest()[:16]}.ply"
    raw = None
    if case.cached_reconstruction is not None:
        raw = mesh_from_bytes(case.cached_reconstruction.read_bytes())
        out["source"] = "cached app reconstruction"
    elif key.exists():
        raw = mesh_from_bytes(key.read_bytes())
        out["source"] = "harness cache"
    else:
        rec = get_reconstructor()
        if rec is None:
            return {"available": False}
        t = time.time()
        rgba = resize_max_side(load_image(image).rgba, 768)
        raw = rec.reconstruct(rgba, mask)
        CACHE.mkdir(parents=True, exist_ok=True)
        key.write_bytes(mesh_to_bytes(raw))
        out["source"] = f"TripoSR run ({time.time() - t:.0f}s)"
    mesh, iou = canonical_from_reconstruction(raw, mask, DEFAULTS["target_size_mm"])
    dr = depth_ratio(mesh)
    out.update({"available": True, "alignment_iou": iou, "depth_ratio": dr, "mesh": mesh,
                "gate": "pass" if (dr >= MIN_DEPTH_RATIO and iou >= MIN_ALIGNMENT_IOU) else
                ("rejected: flat (depth ratio < %.2f)" % MIN_DEPTH_RATIO if dr < MIN_DEPTH_RATIO else
                 "rejected: alignment IoU < %.2f" % MIN_ALIGNMENT_IOU)})
    return out


# ------------------------------------------------------------------------------ generation


def run_generator(mask, style: Style, title: str, mesh=None, **kw) -> dict:
    """generate_puzzle with the instrumented kit generator; returns model + recorders."""
    import app.services.pipeline as pipeline

    pipeline.KitGenerator = InstrumentedKit  # harness process only
    instrument_engine()
    RECORDERS.clear()
    ENGINE_LOG.clear()
    params = params_for(style, title, **kw)
    t = time.time()
    try:
        out = pipeline.generate_puzzle(mask, params, "Plywood", outputs=set(), mesh=mesh)
    except PuzzleError as exc:
        return {"ok": False, "error": f"{exc.code}: {exc.message}", "seconds": time.time() - t,
                "engine_rejections": list(ENGINE_LOG)}
    except Exception as exc:  # an unhandled production crash is itself a measured outcome
        return {"ok": False, "error": f"UNHANDLED {type(exc).__name__}: {exc}", "seconds": time.time() - t,
                "engine_rejections": list(ENGINE_LOG), "crash": traceback.format_exc()[-1500:]}
    recs = list(RECORDERS)
    return {"ok": True, "model": out.model, "layout": out.layout, "seconds": time.time() - t, "recorders": recs,
            "engine_rejections": list(ENGINE_LOG), "params": params}


# ------------------------------------------------------------------------------ measurement


def region_recall(reference, candidate) -> dict:
    """Share of each anatomical region of the reference covered by the candidate (side view)."""
    from app.geometry.anatomy import Region, analyze

    ref = M.as_area(reference)
    if ref.is_empty or ref.geom_type != "Polygon":
        ref = max(M.parts(ref), key=lambda p: p.area) if M.parts(ref) else None
    if ref is None:
        return {}
    an = analyze(ref)
    h, w = an.labels.shape
    cand = M.rasterize(candidate, an.x0, an.y0, an.res, (h, w))
    out = {}
    groups = {"torso": (Region.TORSO, Region.CHEST, Region.BELLY), "head": (Region.HEAD,), "neck": (Region.NECK,),
              "limb": (Region.LIMB,), "tail": (Region.TAIL,), "extremity": (Region.EXTREMITY,)}
    for name, regs in groups.items():
        sel = np.isin(an.labels, [int(r) for r in regs])
        if sel.sum() > 20:
            out[name] = float(cand[sel].mean())
    return out


def side_block(reference, model_proj, size) -> dict:
    env = M.envelope(model_proj, ENVELOPE_MM)
    return {"raw": M.silhouette_metrics(reference, model_proj, size),
            f"envelope{int(ENVELOPE_MM)}mm": M.silhouette_metrics(reference, env, size)}


def voxel_block(ref_mesh, slabs, extra_slabs, res, label, depth_body=None) -> dict:
    """3D metrics of the kit (and the intended kit) against a reference solid."""
    bounds = [tuple(np.asarray(b) for b in ref_mesh.bounds)] + [A.slab_bounds(s) for s in slabs]
    g = A.grid_for(bounds, res)
    R = A.mesh_occupancy(ref_mesh, g)
    K = A.voxelize(slabs, g)
    out = {"reference": label, "voxel_mm": res, "volume_iou": M.volume_iou(R, K),
           "kit_volume_fraction": float(K.sum() / max(R.sum(), 1)),
           "proj_iou_side": M.projection_iou(R, K, 0), "proj_iou_front": M.projection_iou(R, K, 2),
           "proj_iou_top": M.projection_iou(R, K, 1), **M.surface_distances(R, K, res)}
    if extra_slabs:
        KI = K | A.voxelize(extra_slabs, g)
        out["intended"] = {"volume_iou": M.volume_iou(R, KI), "proj_iou_front": M.projection_iou(R, KI, 2),
                           "proj_iou_top": M.projection_iou(R, KI, 1),
                           **{k: v for k, v in M.surface_distances(R, KI, res).items()
                              if k in ("chamfer_mm", "coverage_6mm", "completeness_mean_mm")}}
    if depth_body is not None:  # the 2.5D body the generator saw (silhouette + symmetric depth)
        sil, depth = depth_body
        D = A.depth_occupancy(sil, depth, g)
        out["generator_body_vs_reference"] = {"volume_iou": M.volume_iou(R, D),
                                              "proj_iou_front": M.projection_iou(R, D, 2),
                                              "proj_iou_top": M.projection_iou(R, D, 1)}
        out["kit_vs_generator_body"] = {"volume_iou": M.volume_iou(D, K), **{
            k: v for k, v in M.surface_distances(D, K, res).items() if k in ("coverage_6mm", "chamfer_mm")}}
    return out


def depth_body_volume(sil, depth, ref_mesh, res) -> dict:
    """Stage metric only: the 2.5D body vs a reference solid (no kit)."""
    g = A.grid_for([tuple(np.asarray(b) for b in ref_mesh.bounds)], res)
    R = A.mesh_occupancy(ref_mesh, g)
    D = A.depth_occupancy(sil, depth, g)
    return {"volume_iou": M.volume_iou(R, D), "proj_iou_front": M.projection_iou(R, D, 2),
            "proj_iou_top": M.projection_iou(R, D, 1), **{k: v for k, v in M.surface_distances(R, D, res).items()
                                                            if k in ("chamfer_mm", "completeness_mean_mm")}}


def mesh_vs_mesh(ref_mesh, cand_mesh, res) -> dict:
    g = A.grid_for([tuple(np.asarray(b) for b in ref_mesh.bounds), tuple(np.asarray(b) for b in cand_mesh.bounds)], res)
    R, C = A.mesh_occupancy(ref_mesh, g), A.mesh_occupancy(cand_mesh, g)
    d = M.surface_distances(R, C, res)
    return {"volume_iou": M.volume_iou(R, C), "chamfer_mm": d.get("chamfer_mm"),
            "proj_iou_front": M.projection_iou(R, C, 2), "proj_iou_top": M.projection_iou(R, C, 1)}


def evaluate_mode(case, mode, gen, reference_2d, generator_input_2d, size, res, ref3d=None, ref3d_label=None,
                  depth_body=None, overlay_dir=None) -> dict:
    if not gen["ok"]:
        return {"status": "failed", "error": gen["error"], "engine_rejections": gen.get("engine_rejections")}
    model = gen["model"]
    slabs = A.model_slabs(model)
    side = A.project(slabs, "side")
    roles: dict[str, int] = {}
    for pc in model.pieces:
        roles[pc.role.value] = roles.get(pc.role.value, 0) + 1
    res_d: dict = {"status": "ok", "seconds": round(gen["seconds"], 1), "pieces": len(model.pieces), "roles": roles,
                   "piece_count_target": gen["params"].piece_count, "orientation": model.orientation.value,
                   "shape_source": model.analysis.get("shape_source"), "warnings": [w for w in model.warnings if "label" not in w]}
    if reference_2d is not None:
        res_d["side_vs_reference"] = side_block(reference_2d, side, size)
        res_d["region_recall_vs_reference"] = region_recall(reference_2d, side)
    if generator_input_2d is not None:
        res_d["side_vs_generator_input"] = side_block(generator_input_2d, side, size)
    # degradation (kit only)
    extra = []
    recs = gen.get("recorders") or []
    if recs:
        final = next((r for r in recs if r.model is model), recs[-1])
        res_d["degradation"] = final.degradation()
        res_d["sheet_fit_attempts"] = len(recs)
        if len(recs) > 1 and recs[0].model is not None and reference_2d is not None:
            first_side = A.project(A.model_slabs(recs[0].model), "side")
            res_d["degradation"]["sheet_fit_reduction"] = {
                "first_pieces": len(recs[0].model.pieces), "final_pieces": len(model.pieces),
                "side_iou_first": M.iou(reference_2d, first_side), "side_iou_final": M.iou(reference_2d, side)}
        crosses, plates = final.intended_extra()
        T = model.params.thickness_mm
        extra = [A.cross_slab(p, f, T, r) for p, f, r in crosses] + [A.plate_slab(p, z, T, r) for p, z, r in plates]
        res_d["degradation"]["intended_extra_pieces"] = len(extra)
        if reference_2d is not None:
            intended_side = A.project(slabs + extra, "side")
            res_d["degradation"]["side_iou_actual"] = M.iou(reference_2d, side)
            res_d["degradation"]["side_iou_intended"] = M.iou(reference_2d, intended_side)
            res_d["degradation"]["side_env_iou_actual"] = M.iou(reference_2d, M.envelope(side, ENVELOPE_MM))
            res_d["degradation"]["side_env_iou_intended"] = M.iou(reference_2d, M.envelope(intended_side, ENVELOPE_MM))
        front = A.project(slabs, "front")
        front_i = A.project(slabs + extra, "front") if extra else front
        res_d["degradation"]["front_area_actual_mm2"] = float(M.as_area(front).area)
        res_d["degradation"]["front_area_intended_mm2"] = float(M.as_area(front_i).area)
    if ref3d is not None:
        res_d["volume_vs_" + ref3d_label] = voxel_block(ref3d, slabs, extra, res, ref3d_label, depth_body)
    if overlay_dir is not None:
        draw_overlay(overlay_dir / f"{case.name}__{mode}.png", reference_2d, side, slabs, ref3d)
    return res_d


# ------------------------------------------------------------------------------ overlays


def mesh_view(mesh, view: str, res: float = 0.5):
    """Orthographic projection of a mesh onto a view plane (all triangles rasterised)."""
    from app.geometry.depth import raster_to_geometry

    i, j = A.VIEWS[view]
    v = mesh.vertices[:, [i, j]]
    lo = v.min(axis=0) - 2
    shape = (int(np.ceil((v[:, 1].max() + 2 - lo[1]) / res)) + 1, int(np.ceil((v[:, 0].max() + 2 - lo[0]) / res)) + 1)
    img = np.zeros(shape, np.uint8)
    tris = np.round((v[mesh.faces] - lo) / res * 4).astype(np.int32)
    cv2.fillPoly(img, list(tris), 1, lineType=cv2.LINE_8, shift=2)
    return raster_to_geometry(img > 0, float(lo[0]), float(lo[1]), res)


def _draw_geom(d: ImageDraw.ImageDraw, geom, T, fill, outline=None):
    for p in M.parts(M.as_area(geom)):
        d.polygon([T(c) for c in p.exterior.coords], fill=fill, outline=outline)
        for h in p.interiors:
            d.polygon([T(c) for c in h.coords], fill=(255, 255, 255, 0))


def draw_overlay(path: Path, reference, side, slabs, ref3d):
    views = [("side", reference, side)]
    if ref3d is not None:
        for view in ("front", "top"):
            views.append((view, mesh_view(ref3d, view), A.project(slabs, view)))
    W, H = 520, 400
    img = Image.new("RGB", (W * len(views), H + 20), "white")
    for k, (name, ref, cand) in enumerate(views):
        geoms = [g for g in (ref, cand) if g is not None and not M.as_area(g).is_empty]
        if not geoms:
            continue
        x0, y0, x1, y1 = M.as_area(geoms[0]).union(M.as_area(geoms[-1])).bounds
        s = min((W - 20) / max(x1 - x0, 1e-6), (H - 20) / max(y1 - y0, 1e-6))

        def T(c, k=k, x0=x0, y1=y1, s=s):
            return (k * W + 10 + (c[0] - x0) * s, 20 + 10 + (y1 - c[1]) * s)

        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        if ref is not None:
            _draw_geom(dl, ref, T, (200, 200, 200, 255))
        _draw_geom(dl, cand, T, (37, 99, 235, 150), (20, 50, 140, 255))
        img.paste(layer, (0, 0), layer)
        ImageDraw.Draw(img).text((k * W + 6, 4), f"{name}: grey = reference, blue = assembled kit", fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


# ------------------------------------------------------------------------------ case driver


def clean_for_json(o):
    if isinstance(o, dict):
        return {k: clean_for_json(v) for k, v in o.items() if k not in ("mesh",)}
    if isinstance(o, (list, tuple)):
        return [clean_for_json(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (isinstance(o, float) and math.isnan(o)) or (isinstance(o, np.floating) and np.isnan(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def run_case(case: Case, res: float, overlay_dir: Path) -> dict:
    from app.geometry.depth import AnatomicalDepthModel
    from app.geometry.volume import MeshVolume
    from app.services.pipeline import decode_mask, extract_silhouette

    size = DEFAULTS["target_size_mm"]
    mf = PuzzleParameters(thickness_mm=DEFAULTS["thickness_mm"]).min_feature_mm
    out: dict = {"case": case.name, "kind": case.kind, "note": case.note, "full_body": case.full_body}
    image, gt_img_mask = photo_bytes(case)
    t = time.time()
    try:
        sil = extract_silhouette(image)
    except PuzzleError as exc:
        out["segmentation"] = {"error": f"{exc.code}: {exc.message}"}
        return out
    mask = decode_mask(sil.mask_png)
    out["segmentation"] = {"method": sil.method, "confidence": sil.confidence, "seconds": round(time.time() - t, 1),
                           "suitable": (sil.suitability or {}).get("suitable"),
                           "suitability_overall": (sil.suitability or {}).get("overall_confidence"),
                           "suitability_blocking": (sil.suitability or {}).get("blocking")}
    tr = traced_normalize(mask, size, mf)
    out["normalization_trace_equals_production"] = tr.production_equal
    seg_all_mm = tr.px_to_mm.apply(tr.all_components_px)
    largest_mm = tr.px_to_mm.apply(tr.largest_px)
    gt_mm = tr.px_to_mm.apply(px_geometry(gt_img_mask, mask)) if gt_img_mask is not None else None
    reference = gt_mm if gt_mm is not None else seg_all_mm
    out["reference_2d"] = "ground-truth mask" if gt_mm is not None else "pipeline segmentation (no ground truth)"

    # ---- 2D stage chain
    stages = {}
    chain = [("segmentation_all_components", seg_all_mm), ("largest_component", largest_mm),
             ("simplified", tr.simplified), ("opened_min_feature", tr.opened), ("reinforced", tr.reinforced),
             ("normalized_final", tr.final)]
    for name, geom in chain:
        if gt_mm is None and name == "segmentation_all_components":
            continue
        stages[name] = M.silhouette_metrics(reference, geom, size)
    out["stages_2d_vs_reference"] = stages

    # ---- 3D references
    gt3d_norm = None
    if case.kind == "gtmesh":
        prepared = gt_prepared(case)
        proj = MeshVolume(prepared).silhouette
        # register the prepared GT mesh onto the normalized silhouette frame (bbox similarity)
        a, b = proj.bounds, M.as_area(gt_mm).bounds
        s = max(b[2] - b[0], b[3] - b[1]) / max(a[2] - a[0], a[3] - a[1])
        gt3d_norm = fresh_copy(prepared)
        Tm = np.eye(4)
        Tm[:3, :3] *= s
        Tm[:3, 3] = [b[0] - a[0] * s, b[1] - a[1] * s, 0.0]
        gt3d_norm.apply_transform(Tm)
        est = AnatomicalDepthModel().estimate(tr.final)
        out["stage_3d_estimated_body_vs_gt"] = depth_body_volume(tr.final, est, gt3d_norm, res)

    # ---- modes
    modes: dict = {}
    est_depth = AnatomicalDepthModel().estimate(tr.final)
    for style, label in STYLES:
        gen = run_generator(mask, style, case.name)
        if not gen["ok"] and style == Style.SOLID and "TOO_MANY_PIECES" in gen["error"]:
            first_err = gen["error"]
            gen = run_generator(mask, style, case.name, piece_count=16)
            if gen["ok"]:
                gen["note"] = f"default 30 pieces failed ({first_err}); measured at 16 pieces"
        r = evaluate_mode(case, f"{label}_estimated", gen, reference, tr.final, size, res,
                          ref3d=gt3d_norm, ref3d_label="gt" if gt3d_norm is not None else None,
                          depth_body=(tr.final, est_depth) if gt3d_norm is not None else None,
                          overlay_dir=overlay_dir)
        if gen.get("note"):
            r["note"] = gen["note"]
        if gt3d_norm is None and gen["ok"]:  # no truth: kit vs the generator's own 2.5D body
            r["volume_vs_own_estimated_body"] = kit_vs_own_body(gen["model"], tr.final, est_depth, res)
        modes[f"{label}_estimated"] = r

    # ---- AI 3D (production default shape_source=auto) for images
    rec = reconstruction(case, image, mask)
    out["reconstruction"] = {k: v for k, v in rec.items() if k != "mesh"}
    if rec.get("available"):
        mesh = rec["mesh"]
        mv = MeshVolume(mesh)
        # Reference outline in the mesh frame: both are scaled so the projected longest side is
        # the puzzle size, centred in x and resting on y = 0 (registration by bounding box).
        ref_in_mesh = seg_all_mm if gt_mm is None else gt_mm
        out["reconstruction"]["projection_vs_reference_2d"] = M.silhouette_metrics(ref_in_mesh, mv.silhouette, size)
        out["reconstruction"]["stage_2p5d_body_vs_reconstruction"] = depth_body_volume(mv.silhouette, mv.depth, mesh, res)
        if gt3d_norm is not None:
            out["reconstruction"]["reconstruction_vs_gt"] = mesh_vs_mesh(gt3d_norm, mesh, res)
        if rec["gate"] == "pass":
            for style, label in STYLES:
                gen = run_generator(mask, style, case.name, mesh=mesh)
                if not gen["ok"] and style == Style.SOLID and "TOO_MANY_PIECES" in gen["error"]:
                    gen = run_generator(mask, style, case.name, mesh=mesh, piece_count=16)
                    if gen["ok"]:
                        gen["note"] = "default 30 pieces failed; measured at 16 pieces"
                r = evaluate_mode(case, f"{label}_ai3d", gen, ref_in_mesh, mv.silhouette, size, res,
                                  ref3d=gt3d_norm if gt3d_norm is not None else mesh,
                                  ref3d_label="gt" if gt3d_norm is not None else "reconstruction",
                                  depth_body=(mv.silhouette, mv.depth), overlay_dir=overlay_dir)
                if gen.get("note"):
                    r["note"] = gen["note"]
                modes[f"{label}_ai3d"] = r
        else:
            modes["ai3d"] = {"status": "not used by production", "reason": rec["gate"]}

    # ---- 3D-model upload path (GT meshes only)
    if case.kind == "gtmesh":
        gen_mesh, gt_frame = upload_path(case)
        out["upload_remesh_vs_gt"] = mesh_vs_mesh(gt_frame, gen_mesh, res)
        mv = MeshVolume(gen_mesh)
        out["upload_2p5d_body_vs_gt"] = depth_body_volume(mv.silhouette, mv.depth, gt_frame, res)
        gt_side = MeshVolume(gt_frame).silhouette
        for style, label in STYLES:
            gen = run_generator(mask, style, case.name, mesh=gen_mesh)
            if not gen["ok"] and style == Style.SOLID and "TOO_MANY_PIECES" in gen["error"]:
                gen = run_generator(mask, style, case.name, mesh=gen_mesh, piece_count=16)
                if gen["ok"]:
                    gen["note"] = "default 30 pieces failed; measured at 16 pieces"
            r = evaluate_mode(case, f"{label}_upload3d", gen, gt_side, mv.silhouette, size, res,
                              ref3d=gt_frame, ref3d_label="gt", depth_body=(mv.silhouette, mv.depth),
                              overlay_dir=overlay_dir)
            if gen.get("note"):
                r["note"] = gen["note"]
            modes[f"{label}_upload3d"] = r
    out["modes"] = modes
    return out


def kit_vs_own_body(model, sil, depth, res) -> dict:
    slabs = A.model_slabs(model)
    bounds = [A.slab_bounds(s) for s in slabs]
    minx, miny, maxx, maxy = sil.bounds
    zmax = float(depth.values.max()) + 2
    bounds.append((np.array([minx, miny, -zmax]), np.array([maxx, maxy, zmax])))
    g = A.grid_for(bounds, res)
    D = A.depth_occupancy(sil, depth, g)
    K = A.voxelize(slabs, g)
    d = M.surface_distances(D, K, res)
    return {"reference": "generator's own estimated 2.5D body", "volume_iou": M.volume_iou(D, K),
            "proj_iou_front": M.projection_iou(D, K, 2), "proj_iou_top": M.projection_iou(D, K, 1),
            "coverage_6mm": d.get("coverage_6mm"), "chamfer_mm": d.get("chamfer_mm")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--styles", default="kit,solid", choices=sorted(STYLE_SETS))
    ap.add_argument("--res", type=float, default=1.0)
    ap.add_argument("--out", default=str(BACKEND / "fidelity" / "reports" / "phase2"))
    args = ap.parse_args()
    global STYLES
    STYLES = STYLE_SETS[args.styles]
    out_dir = Path(args.out)
    (out_dir / "cases").mkdir(parents=True, exist_ok=True)
    only = {s for s in args.only.split(",") if s}
    for case in all_cases():
        if only and case.name not in only:
            continue
        t = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] {case.name} ...", flush=True)
        try:
            result = run_case(case, args.res, out_dir / "overlays")
        except Exception as exc:  # keep going; the failure itself is a result
            result = {"case": case.name, "kind": case.kind, "harness_error": repr(exc),
                      "traceback": traceback.format_exc()}
        result["harness_seconds"] = round(time.time() - t, 1)
        (out_dir / "cases" / f"{case.name}.json").write_text(json.dumps(clean_for_json(result), indent=1))
        print(f"    done in {result['harness_seconds']}s", flush=True)


if __name__ == "__main__":
    main()
