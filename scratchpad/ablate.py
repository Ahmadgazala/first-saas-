"""Ablation of the latest slice changes on one (case, mode). Measurement only: production code is untouched,
variants are subclasses of SliceGenerator patched into the pipeline in this process.

    python ablate.py <case> <estimated|ai3d> <out.jsonl>
"""
import json
import sys
import warnings
import logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives

import numpy as np

import app.services.pipeline as P
from app.geometry.depth import AnatomicalDepthModel
from app.geometry.kit.slice import SliceGenerator
from app.geometry.normalize import fill_small_holes
from app.geometry.types import PuzzleParameters, Style
from app.geometry.volume import MeshVolume
from fidelity import assembly as A
from fidelity import metrics as M
from fidelity.cases import all_cases
from fidelity.run import (DEFAULTS, fresh_copy, gt_prepared, kit_vs_own_body, params_for, photo_bytes,
                          px_geometry, reconstruction, voxel_block)
from fidelity.stages import traced_normalize

case_name, mode, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
only = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None
size = DEFAULTS["target_size_mm"]
mf = PuzzleParameters(thickness_mm=3.0).min_feature_mm
case = next(c for c in all_cases() if c.name == case_name)
image, gt_img = photo_bytes(case)
sil = P.extract_silhouette(image)
mask = P.decode_mask(sil.mask_png)
tr = traced_normalize(mask, size, mf)
est = AnatomicalDepthModel().estimate(tr.final)
seg_all_mm = tr.px_to_mm.apply(tr.all_components_px)
gt_mm = tr.px_to_mm.apply(px_geometry(gt_img, mask)) if gt_img is not None else None
ref2d = gt_mm if gt_mm is not None else seg_all_mm

gt3d_norm = None
if case.kind == "gtmesh":
    prepared = gt_prepared(case)
    proj = MeshVolume(prepared).silhouette
    a, b = proj.bounds, M.as_area(gt_mm).bounds
    s = max(b[2] - b[0], b[3] - b[1]) / max(a[2] - a[0], a[3] - a[1])
    gt3d_norm = fresh_copy(prepared)
    Tm = np.eye(4)
    Tm[:3, :3] *= s
    Tm[:3, 3] = [b[0] - a[0] * s, b[1] - a[1] * s, 0.0]
    gt3d_norm.apply_transform(Tm)

mesh = None
if mode == "ai3d":
    rec = reconstruction(case, image, mask)
    mesh = rec["mesh"]
    mv = MeshVolume(mesh)

FIELDS = ("thin", "trace", "skip", "grow", "holes")
BASE = dict(thin=False, trace=False, skip=False, grow=False, holes=False)  # state before the task (holes filled)
FULL = dict(thin=True, trace=True, skip=True, grow=True, holes=True)


def make(v):
    class V(SliceGenerator):
        grow_plate_in_narrow = v["grow"]

        def __init__(self, params, depth_model=None):
            super().__init__(params, depth_model)
            self.analyzer.thin_appendages = v["thin"]
            self.trace_head = v["trace"]
            self.invent_openings = not v["trace"]  # before: generated eye/nostril; after: traced only

        def generate(self, silhouette, *a, **k):
            if not v["holes"]:
                silhouette = fill_small_holes(silhouette)
            return super().generate(silhouette, *a, **k)

        if not v["skip"]:
            def _narrow(self):
                return np.zeros(len(self.st.spine.s), bool)
    return V


def measure(v):
    P.SliceGenerator = make(v)
    params = params_for(Style.SLICE, "ablate")
    try:
        out = P.generate_puzzle(mask, params, "Plywood", outputs=set(), mesh=mesh)
    except Exception as exc:  # recorded, not hidden
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    model = out.model
    slabs = A.model_slabs(model)
    side = A.project(slabs, "side")
    r = {"pieces": len(model.pieces), "roles": model.analysis["component_counts"],
         "slices": model.analysis.get("slices"), "traced": model.analysis.get("traced"),
         "side_iou": M.iou(ref2d if mode == "estimated" else ref2d, side),
         "warnings": [w[:110] for w in model.warnings if "label" not in w and "target" not in w]}
    if mode == "estimated":
        d = kit_vs_own_body(model, tr.final, est, 1.0)
    else:
        ref3d = gt3d_norm if gt3d_norm is not None else mesh
        d = voxel_block(ref3d, slabs, None, 1.0, "ref", depth_body=None)
    r.update({k: d.get(k) for k in ("proj_iou_front", "proj_iou_top", "proj_iou_side", "coverage_6mm", "chamfer_mm",
                                    "volume_iou", "material_outside_ref")})
    return r


variants = {"BASE": BASE, "FULL": FULL}
for f in FIELDS:
    if f == "holes" and mode == "ai3d":
        continue
    variants[f"+{f}"] = {**BASE, f: True} if f != "skip" else {**BASE, "skip": True, "grow": True}
    variants[f"-{f}"] = {**FULL, f: False} if f != "skip" else {**FULL, "skip": False, "grow": False}
variants["+skip_only"] = {**BASE, "skip": True}  # skip slices but keep the plate at band height
if only:
    variants = {k: v for k, v in variants.items() if k in only}
with open(out_path, "a") as fh:
    for name, v in variants.items():
        res = measure(v)
        fh.write(json.dumps({"case": case_name, "mode": mode, "variant": name, **res}, default=float) + "\n")
        fh.flush()
