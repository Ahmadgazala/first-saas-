"""Is 'half-depth at the spine point' a valid test for 'no slice can be cut here'? Compare it with the real section
width of slices that the BASELINE generator did cut. Measurement only."""
import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
import app.services.pipeline as P
from app.geometry.kit.slice import SliceGenerator
from app.geometry.normalize import fill_small_holes
from app.geometry.types import Style
from app.geometry.anatomy import Region
from fidelity.cases import all_cases
from fidelity.run import params_for, photo_bytes, reconstruction

INST = []
class Base(SliceGenerator):          # the state before the narrow-region rule
    def __init__(self, p, dm=None):
        super().__init__(p, dm); INST.append(self)
    def _narrow(self): return np.zeros(len(self.st.spine.s), bool)

for name, use_mesh in (("gt_dino", True), ("photo_penguin", True), ("photo_trex_kit", True), ("photo_trex_kit", False), ("photo_cat_snow", False)):
    case = next(c for c in all_cases() if c.name == name)
    image, _ = photo_bytes(case)
    mask = P.decode_mask(P.extract_silhouette(image).mask_png)
    mesh = reconstruction(case, image, mask)["mesh"] if use_mesh else None
    P.SliceGenerator = Base; INST.clear()
    out = P.generate_puzzle(mask, params_for(Style.SLICE, "n"), "Plywood", outputs=set(), mesh=mesh)
    g = INST[-1]; sp = g.st.spine; wmin = g._min_slice_width()
    ribs = g.rib_log
    rows = []
    for r in ribs:
        s = r["s"]; i = sp.index(s)
        flagged = (2 * max(sp.half_depth[i], 0) < wmin) and sp.region[i] != int(Region.HEAD)
        rows.append((s, 2 * sp.half_depth[i], r["section_u_extent_mm"], r["u_scale"], flagged))
    fl = [x for x in rows if x[4]]
    print(f"{name:15s} {'AI-3D mesh' if use_mesh else 'estimated  '}  slices placed {len(rows):2d};  would be skipped by the rule: {len(fl):2d}"
          + (f"  | those slices: spine 2*half-depth median {np.median([x[1] for x in fl]):.1f} mm vs real section width median {np.median([x[2] for x in fl]):.1f} mm (min {min(x[2] for x in fl):.1f}, slot+walls needs {wmin:.1f})" if fl else ""))
    P.SliceGenerator = SliceGenerator
