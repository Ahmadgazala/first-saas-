import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
import app.services.pipeline as P
from app.geometry.kit.slice import SliceGenerator
from app.geometry.types import Style, PuzzleParameters
from app.geometry.depth import AnatomicalDepthModel
from fidelity.cases import all_cases
from fidelity.run import params_for, photo_bytes, kit_vs_own_body
from fidelity.stages import traced_normalize
from fidelity import assembly as A, metrics as M
case = next(c for c in all_cases() if c.name == "photo_cat_snow")
image, _ = photo_bytes(case); mask = P.decode_mask(P.extract_silhouette(image).mask_png)
tr = traced_normalize(mask, 250.0, PuzzleParameters(thickness_mm=3.0).min_feature_mm); est = AnatomicalDepthModel().estimate(tr.final)
INST = []
class Base(SliceGenerator):
    def __init__(self, p, dm=None): super().__init__(p, dm); INST.append(self)
    def _narrow(self): return np.zeros(len(self.st.spine.s), bool)
class Full(SliceGenerator):
    def __init__(self, p, dm=None): super().__init__(p, dm); INST.append(self)
res = {}
for name, cls in (("BASE", Base), ("FULL", Full)):
    P.SliceGenerator = cls
    out = P.generate_puzzle(mask, params_for(Style.SLICE, "c"), "Plywood", outputs=set())
    g = INST[-1]; m = out.model
    d = kit_vs_own_body(m, tr.final, est, 1.0)
    res[name] = (g, m)
    sp = g.st.spine; nar = np.nonzero(Full._narrow(g))[0]
    rng = (round(float(sp.s[nar[0]]), 1), round(float(sp.s[nar[-1]]), 1)) if len(nar) else None
    print(f"{name}: top {d['proj_iou_top']:.3f} front {d['proj_iou_front']:.3f}  narrow s-range {rng}  spine length {sp.length:.0f}")
    print("   slices (s, width mm):", [(r['s'], round(r['section_u_extent_mm'])) for r in g.rib_log])
    print("   spine plates:", [(p.id, round(p.polygon.area), [round(v) for v in p.polygon.bounds]) for p in m.pieces if p.role.value == 'spine'])
# top-view footprints
gB, mB = res["BASE"]; gF, mF = res["FULL"]
minx, miny, maxx, maxy = tr.final.bounds
zmax = float(est.values.max()) + 2
bounds = [A.slab_bounds(s) for s in A.model_slabs(mB) + A.model_slabs(mF)] + [(np.array([minx, miny, -zmax]), np.array([maxx, maxy, zmax]))]
gr = A.grid_for(bounds, 1.0)
D = A.depth_occupancy(tr.final, est, gr)
KB = A.voxelize(A.model_slabs(mB), gr); KF = A.voxelize(A.model_slabs(mF), gr)
tp = lambda K: K.any(axis=1)   # harness 'top' = axis 1
Dt, Bt, Ft = tp(D), tp(KB), tp(KF)
print("top-view pixels: reference", int(Dt.sum()), "BASE", int(Bt.sum()), "FULL", int(Ft.sum()), "| BASE only", int((Bt & ~Ft).sum()), "FULL only", int((Ft & ~Bt).sum()),
      "| inside reference: BASE", int((Bt & Dt).sum()), "FULL", int((Ft & Dt).sum()), "| outside reference: BASE", int((Bt & ~Dt).sum()), "FULL", int((Ft & ~Dt).sum()))
print("roles BASE", mB.analysis['component_counts'], "FULL", mF.analysis['component_counts'])
only = Bt & ~Ft
ys, xs = np.nonzero(only)
print("BASE-only top-view pixels: x range", round(gr.x0 + xs.min() * gr.res), round(gr.x0 + xs.max() * gr.res), " z range", round(gr.z0 + ys.min() * gr.res) if hasattr(gr, 'z0') else (ys.min(), ys.max()))
sl = lambda m: sorted(((p.role.value, p.id, round(p.position_mm, 1) if p.position_mm is not None else None, [round(v) for v in p.polygon.bounds]) for p in m.pieces if p.role.value in ('limb', 'spacer', 'skull', 'jaw')))
print("BASE limb/spacer/skull:", sl(mB)); print("FULL limb/spacer/skull:", sl(mF))
print("warnings FULL:", [w[:100] for w in mF.warnings if 'label' not in w])
print("warnings BASE:", [w[:100] for w in mB.warnings if 'label' not in w])
print("--- tilt of every slice (deg from the spine normal)")
for nm in ("BASE", "FULL"):
    g, m = res[nm]
    print(nm, [(r['s'], r['tilt_deg']) for r in g.rib_log])
# top-view footprint of each BASE slice that FULL lacks
