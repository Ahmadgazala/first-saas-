import sys, inspect, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, box
import app.services.pipeline as P
from app.geometry.kit import builder as B
from app.geometry.kit.slice import SliceGenerator
from app.geometry.normalize import normalize_silhouette
from app.geometry.types import PieceRole
from fidelity.run import params_for
from app.geometry.types import Style
from pathlib import Path

# number the "return False" exits of _try_cross_unsafe so a rejection can be named
src = inspect.getsource(B.KitGenerator._try_cross_unsafe).splitlines()
out, n, names = [], 0, {}
for i, ln in enumerate(src):
    if "return False" in ln:
        n += 1
        names[n] = src[i - 1].strip()[:95]
        ln = ln.replace("return False", f"return self._fail({n})")
    out.append(ln[4:])
ns = {}
exec("\n".join(out), B.__dict__, ns)

LOG = []
class Dbg(SliceGenerator):
    _try_cross_unsafe = ns["_try_cross_unsafe"]
    _cur = None
    def _fail(self, n):
        if self._cur is not None:
            self._cur["fails"].append(n)
        return False
    def _try_cross(self, part):
        if part.role == PieceRole.SPACER and part.region == "head":
            self._cur = {"s": round(float(part.station), 1), "frame": (round(part.frame.cx, 1), round(part.frame.cy, 1)), "fails": []}
            ok = super()._try_cross(part)
            self._cur["ok"] = ok
            LOG.append(self._cur); self._cur = None
            return ok
        return super()._try_cross(part)
    def _skull(self):
        super()._skull()
        info = self.skull_info
        if info:
            base, e1, e2, Lh, eta_m = self.head_frame
            self.report = dict(Lh=round(Lh, 1), eta_m=round(eta_m, 1), spacer_xi=[round(x, 1) for x in info["spacer_xi"]],
                               spacer_xi_default=info.get("spacer_xi_default"), zs=round(info["zs"], 1))
            # traced openings in the head frame
            e1_, e2_ = e1, e2
            to_local = [e1[0], e1[1], e2[0], e2[1], -float(np.dot(e1, base)), -float(np.dot(e2, base))]
            self.hole_local = [affinity.affine_transform(Polygon(r), to_local) for r in self.st.head.polygon.interiors]
            self.to_local = to_local

mask = np.array(__import__("PIL.Image", fromlist=["Image"]).open("trex_mask_holes.png"))
sil = normalize_silhouette(mask, 250.0, 2.5).polygon
print("silhouette interiors:", [(round(Polygon(r).area, 1), [round(v, 1) for v in Polygon(r).centroid.coords[0]]) for r in sil.interiors])
g = Dbg(params_for(Style.SLICE, "t"))
m = g.generate(sil)
print("head openings traced:", m.analysis["traced"])
print("head frame:", getattr(g, "report", None))
for h in getattr(g, "hole_local", []):
    print("  hole in head frame: xi %.1f..%.1f  eta %.1f..%.1f  area %.1f" % (h.bounds[0], h.bounds[2], h.bounds[1], h.bounds[3], h.area))
print("skull spacer attempts (every _try_cross on a head spacer):")
for r in LOG:
    why = sorted({names[f][:70] for f in r["fails"]})
    print("  station s=%6.1f at %s  ok=%s  rejections: %s" % (r["s"], r["frame"], r["ok"], why or "-"))
print("final spacers:", [(p.id, p.region) for p in m.pieces if p.role == PieceRole.SPACER])
for p in m.pieces:
    if p.role in (PieceRole.SKULL, PieceRole.JAW):
        print(" ", p.id, "interiors", len(p.polygon.interiors), "slots", len(p.slots), "area", round(p.polygon.area))
# does a skull slot touch the traced opening?
sk = [p for p in m.pieces if p.role == PieceRole.SKULL][0]
for h in getattr(g, "hole_local", []):
    pass
print("warnings:", [w[:100] for w in m.warnings if "skull" in w or "jaw" in w or "mouth" in w])
base, e1, e2, Lh, eta_m = g.head_frame
print("--- attempts in head-frame coordinates (xi along head, eta dorsal); hole at xi 58.4..64.4, eta 12.2..19.9; mouth seam eta_m=%.1f" % eta_m)
for r in LOG:
    p = np.array(r["frame"]) - base
    print("  attempt xi=%.1f eta=%.1f  ok=%s  rejected-by=%s" % (p @ e1, p @ e2, r["ok"], sorted({names[f][:48] for f in r["fails"]}) or "-"))
sp = g.st.spine
print("spine (head-frame eta) at xi=19,47.5,58.9:", [round(float(((sp.pts[int(np.argmin(np.abs(((sp.pts - base) @ e1) - x)))] - base) @ e2)), 1) for x in (19, 47.5, 58.9)])
sk_poly = [p for p in m.pieces if p.role == PieceRole.SKULL][0]
# the slot channel of the skull plate in head-frame coordinates
for sl in [s for p in m.pieces if p.role == PieceRole.SKULL for s in p.slots][:1]:
    ch = sl.channel()
    print("skull slot channel bounds (world) ", [round(v, 1) for v in ch.bounds], "depth", round(sl.depth_mm, 1))
