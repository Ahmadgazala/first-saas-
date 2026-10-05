import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
import app.services.pipeline as P
from app.geometry.depth import AnatomicalDepthModel
from app.geometry.types import PuzzleParameters, Style, PieceRole
from fidelity import assembly as A, metrics as M
from fidelity.cases import all_cases
from fidelity.run import DEFAULTS, params_for, photo_bytes
from fidelity.stages import traced_normalize

case = next(c for c in all_cases() if c.name == "photo_trex_kit")
image, _ = photo_bytes(case)
mask = P.decode_mask(P.extract_silhouette(image).mask_png)
mf = PuzzleParameters(thickness_mm=3.0).min_feature_mm
tr = traced_normalize(mask, 250.0, mf)
est = AnatomicalDepthModel().estimate(tr.final)
out = P.generate_puzzle(mask, params_for(Style.SLICE, "t"), "Plywood", outputs=set())
model = out.model
slabs = A.model_slabs(model)
arm_ids = [p.id for p in model.pieces if p.role == PieceRole.LIMB and (p.polygon.bounds[3] - p.polygon.bounds[1]) < 50]
leg_ids = [p.id for p in model.pieces if p.role == PieceRole.LIMB and p.id not in arm_ids]
print("arm plates", arm_ids, "legs", leg_ids)
for p in model.pieces:
    if p.id in arm_ids + leg_ids:
        print(p.id, "z", round(p.position_mm, 1), "bbox", [round(v, 1) for v in p.polygon.bounds], "area", round(p.polygon.area))
by = {s.pid: s for s in slabs}
sl_arm = [by[i] for i in arm_ids]; sl_rest = [s for s in slabs if s.pid not in arm_ids]
minx, miny, maxx, maxy = tr.final.bounds
zmax = float(est.values.max()) + 2
bounds = [A.slab_bounds(s) for s in slabs] + [(np.array([minx, miny, -zmax]), np.array([maxx, maxy, zmax]))]
g = A.grid_for(bounds, 1.0)
D = A.depth_occupancy(tr.final, est, g)
Kall = A.voxelize(slabs, g); Krest = A.voxelize(sl_rest, g); Karm = A.voxelize(sl_arm, g)
print("axes: [z][y][x]; front = project along x?  (harness uses axis=2 for 'front')")
for label, K in (("all", Kall), ("without arm plates", Krest)):
    print(f"{label:20s} front IoU {M.projection_iou(D, K, 2):.3f} top IoU {M.projection_iou(D, K, 1):.3f} cov6 {M.surface_distances(D, K, 1.0)['coverage_6mm']:.3f}")
print("arm voxels", int(Karm.sum()), "outside reference body:", int((Karm & ~D).sum()), f"({(Karm & ~D).sum() / max(Karm.sum(), 1):.2f})")
F = lambda K: K.any(axis=2)
Dp, Kp, Rp, Ap = F(D), F(Kall), F(Krest), F(Karm)
print("front-view pixels: reference", int(Dp.sum()), "kit(all)", int(Kp.sum()), "kit(no arms)", int(Rp.sum()),
      "arm-only pixels", int(Ap.sum()), "arm pixels outside ref", int((Ap & ~Dp).sum()), "arm pixels outside ref and outside other kit parts", int((Ap & ~Dp & ~Rp).sum()))
# where is the reference body wide at the arm height / station?
ay = np.mean([(by[i].polygon.bounds[1] + by[i].polygon.bounds[3]) / 2 for i in arm_ids])
ax = np.mean([ (by[i].polygon.bounds[0] + by[i].polygon.bounds[2]) / 2 for i in arm_ids])
print("arm plate centre in its own frame", round(ax, 1), round(ay, 1))

print("---- which pieces cost the front view: remove arm plates and each spacer in turn")
sp_ids = [p.id for p in model.pieces if p.role == PieceRole.SPACER]
for p in model.pieces:
    if p.role == PieceRole.SPACER:
        print(" ", p.id, "region", p.region, "frame", (round(p.frame.cx), round(p.frame.cy)), "u-extent (z)", [round(v, 1) for v in (p.polygon.bounds[0], p.polygon.bounds[2])])
def fr(drop):
    K = A.voxelize([s for s in slabs if s.pid not in drop], g)
    return M.projection_iou(D, K, 2), M.projection_iou(D, K, 1)
print("  all pieces                    front %.3f top %.3f" % fr(set()))
print("  - arm plates                  front %.3f top %.3f" % fr(set(arm_ids)))
for sid in sp_ids:
    print("  - arm plates - %s             front %.3f top %.3f" % (sid, *fr(set(arm_ids) | {sid})))
print("estimated half-depth of the reference body at the arm plate (x,y):", [round(float(est.sample(np.array([x]), np.array([y]))[0]), 1) for x, y in ((-52, 128), (-46, 133), (-60, 120), (-35, 130))],
      "| max half-depth anywhere:", round(float(est.values.max()), 1), "| arm plate z = +-24.7, arm crossbar half-span 29.2")
