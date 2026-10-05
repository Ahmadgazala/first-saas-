import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
import app.services.pipeline as P
from app.geometry.kit.slice import SliceGenerator
from app.geometry.types import Style
from app.geometry.volume import MeshVolume
from app.geometry.anatomy import Region
from fidelity.cases import all_cases
from fidelity.run import params_for, photo_bytes, reconstruction
INST = []
class Base(SliceGenerator):
    def __init__(self, p, dm=None): super().__init__(p, dm); INST.append(self)
    def _narrow(self): return np.zeros(len(self.st.spine.s), bool)
for name in ("gt_dino", "photo_penguin", "photo_trex_kit"):
    case = next(c for c in all_cases() if c.name == name)
    image, _ = photo_bytes(case); mask = P.decode_mask(P.extract_silhouette(image).mask_png)
    mesh = reconstruction(case, image, mask)["mesh"]
    P.SliceGenerator = Base; INST.clear()
    P.generate_puzzle(mask, params_for(Style.SLICE, "n"), "Plywood", outputs=set(), mesh=mesh)
    g = INST[-1]; sp = g.st.spine; v = g.volume; wmin = g._min_slice_width()
    print(f"== {name}: mesh z-extent {np.round(mesh.bounds[:, 2], 1).tolist()}, centre-of-mass plane z=0")
    n = 0
    for r in g.rib_log:
        i = sp.index(r["s"]); flagged = 2 * sp.half_depth[i] < wmin and sp.region[i] != int(Region.HEAD)
        if not flagged: continue
        x, y = sp.pts[i]
        ivs = v.z_intervals(float(x), float(y), half_width=1.0)
        print(f"   s={r['s']:6.1f} spine point ({x:6.1f},{y:6.1f}): body z-intervals along the spine point {[(round(a,1), round(b,1)) for a,b in ivs]}  -> min(up,down) around z=0 gives half-depth {sp.half_depth[i]:.1f}; slice actually cut {r['section_u_extent_mm']:.1f} mm wide, centre u={r['section_u_centre_mm']:+.1f}")
        n += 1
        if n >= 4: break
    P.SliceGenerator = SliceGenerator
