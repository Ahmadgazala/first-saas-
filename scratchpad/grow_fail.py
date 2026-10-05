import sys, inspect, warnings, logging, collections
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
import app.services.pipeline as P
from app.geometry.kit import builder as B
from app.geometry.kit.slice import SliceGenerator
from app.geometry.types import Style, PieceRole
from fidelity.cases import all_cases
from fidelity.run import params_for, photo_bytes, reconstruction
src = inspect.getsource(B.KitGenerator._try_cross_unsafe).splitlines()
out, n, names = [], 0, {}
for i, ln in enumerate(src):
    if "return False" in ln:
        n += 1; names[n] = src[i - 1].strip()[:80]; ln = ln.replace("return False", f"return self._fail({n})")
    out.append(ln[4:])
ns = {}; exec("\n".join(out), B.__dict__, ns)
name = sys.argv[1]
case = next(c for c in all_cases() if c.name == name)
image, _ = photo_bytes(case); mask = P.decode_mask(P.extract_silhouette(image).mask_png)
mesh = reconstruction(case, image, mask)["mesh"]
for label, grow in (("skip only (plate at band height)", False), ("skip + plate grows", True)):
    C = collections.Counter(); placed = [0]
    class V(SliceGenerator):
        grow_plate_in_narrow = grow
        _try_cross_unsafe = ns["_try_cross_unsafe"]
        _cur = False
        def _fail(self, k):
            if self._cur: C[names[k]] += 1
            return False
        def _try_cross(self, part):
            self._cur = part.role in (PieceRole.RIB, PieceRole.VERTEBRA)
            ok = super()._try_cross(part); self._cur = False
            return ok
    P.SliceGenerator = V
    o = P.generate_puzzle(mask, params_for(Style.SLICE, "n"), "Plywood", outputs=set(), mesh=mesh)
    print(f"{name} [{label}] pieces {len(o.model.pieces)} slices placed {o.model.analysis['slices']['placed']}; rejection counts for slice candidates:")
    for k, v in C.most_common(6): print(f"     {v:4d}  {k}")
