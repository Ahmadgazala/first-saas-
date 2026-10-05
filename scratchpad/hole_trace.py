import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np
from PIL import Image
from app.geometry.kit.slice import SliceGenerator
from app.geometry.normalize import normalize_silhouette
from app.geometry.types import PieceRole, Style
from fidelity.run import params_for

class G(SliceGenerator):
    def _traced_mouth(self, H, Lh, eta_default, spine_xy):
        print("H interiors before mouth tracing:", len(H.interiors), "| H bounds", [round(v, 1) for v in H.bounds])
        r = super()._traced_mouth(H, Lh, eta_default, spine_xy)
        if r is None: print("traced mouth: None")
        else: print("traced upper interiors", len(r[0].interiors), "jaw interiors", len(r[1].interiors), "eta_m", round(r[2], 1))
        return r
    def _skull(self):
        print("head.polygon interiors:", len(self.st.head.polygon.interiors))
        super()._skull()
        for k, pl in self.plates.items():
            if pl.role in (PieceRole.SKULL, PieceRole.JAW): print("  after _skull", pl.role.value, "interiors", len(pl.poly.interiors), "slots", len(pl.slots))
    def _skull_spacers(self):
        super()._skull_spacers()
        for k, pl in self.plates.items():
            if pl.role in (PieceRole.SKULL, PieceRole.JAW): print("  after spacers", pl.role.value, "interiors", len(pl.poly.interiors), "slots", len(pl.slots))
sil = normalize_silhouette(np.array(Image.open("trex_mask_holes.png")), 250.0, 2.5).polygon
g = G(params_for(Style.SLICE, "t")); m = g.generate(sil)

print("---- mouth-gap scan along the head (largest gap between stretches of material at each xi)")
from app.geometry.sections import line_intervals
class H2(SliceGenerator):
    def _traced_mouth(self, H, Lh, eta_default, spine_xy):
        xs = np.linspace(0.25 * Lh, 0.97 * Lh, 48)
        for xi in xs:
            ivs = line_intervals(H, float(xi))
            gaps = [(round(a1, 1), round(b0, 1), round(b0 - a1, 1)) for (a0, a1), (b0, b1) in zip(ivs[:-1], ivs[1:])]
            best = max(gaps, key=lambda g: g[2]) if gaps else None
            print(f"  xi {xi:5.1f}: {len(ivs)} stretches; gaps {gaps}; chosen (largest) {best}")
        return super()._traced_mouth(H, Lh, eta_default, spine_xy)
g2 = H2(params_for(Style.SLICE, "t")); g2.generate(sil)
