import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
import app.services.pipeline as P
from app.geometry.depth import AnatomicalDepthModel
from app.geometry.types import PuzzleParameters, Style, PieceRole
from fidelity import assembly as A, metrics as M
from fidelity.cases import all_cases
from fidelity.run import params_for, photo_bytes
from fidelity.stages import traced_normalize
for name in sys.argv[1:]:
    case = next(c for c in all_cases() if c.name == name)
    image, _ = photo_bytes(case); mask = P.decode_mask(P.extract_silhouette(image).mask_png)
    tr = traced_normalize(mask, 250.0, PuzzleParameters(thickness_mm=3.0).min_feature_mm); est = AnatomicalDepthModel().estimate(tr.final)
    model = P.generate_puzzle(mask, params_for(Style.SLICE, "t"), "Plywood", outputs=set()).model
    slabs = A.model_slabs(model)
    arms = [p for p in model.pieces if p.role == PieceRole.LIMB and p.region == "appendage"]
    cb = []
    for p in model.pieces:
        if p.role == PieceRole.SPACER and p.region == "limb":
            # arm girdle crossbar: the spacer closest to an arm plate along the axis
            d = min((np.hypot(p.frame.cx - (a.polygon.centroid.x), p.frame.cy - (a.polygon.centroid.y)) for a in arms), default=1e9)
            cb.append((d, p))
    cb = [p for d, p in sorted(cb, key=lambda t: t[0])[:1]] if arms else []
    minx, miny, maxx, maxy = tr.final.bounds; zmax = float(est.values.max()) + 2
    g = A.grid_for([A.slab_bounds(s) for s in slabs] + [(np.array([minx, miny, -zmax]), np.array([maxx, maxy, zmax]))], 1.0)
    D = A.depth_occupancy(tr.final, est, g)
    def fr(drop): 
        K = A.voxelize([s for s in slabs if s.pid not in drop], g); return M.projection_iou(D, K, 2), M.projection_iou(D, K, 1)
    ids = {a.id for a in arms}; cid = {p.id for p in cb}
    print(f"{name}: arm/appendage plates {[(a.id, round(a.position_mm,1), [round(v) for v in a.polygon.bounds]) for a in arms]}; crossbar {[(p.id, round(p.polygon.bounds[2],1)) for p in cb]}")
    ax = [ (a.polygon.centroid.x, a.polygon.centroid.y) for a in arms]
    print("   reference half-depth at the arm plates:", [round(float(est.sample(np.array([x]), np.array([y]))[0]), 1) for x, y in ax], "| max anywhere", round(float(est.values.max()), 1))
    print("   front/top IoU  all %.3f/%.3f | - appendage plates %.3f/%.3f | - plates - crossbar %.3f/%.3f" % (*fr(set()), *fr(ids), *fr(ids | cid)))
