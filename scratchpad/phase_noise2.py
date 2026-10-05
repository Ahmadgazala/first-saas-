"""Noise floor: shift only the start of the slice lattice by a few mm and watch the 3D metrics move."""
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
name = sys.argv[1]
case = next(c for c in all_cases() if c.name == name)
image, _ = photo_bytes(case); mask = P.decode_mask(P.extract_silhouette(image).mask_png)
tr = traced_normalize(mask, 250.0, PuzzleParameters(thickness_mm=3.0).min_feature_mm); est = AnatomicalDepthModel().estimate(tr.final)
def make(delta, narrow_on, thin=True):
    class V(SliceGenerator):
        def __init__(self, params, dm=None):
            super().__init__(params, dm)
            self.analyzer.thin_appendages = thin
        def _ribs_and_vertebrae(self, plan):
            self.s_lo += delta
            super()._ribs_and_vertebrae(plan)
        if not narrow_on:
            def _narrow(self): return np.zeros(len(self.st.spine.s), bool)
    return V
for thin in (False, True):
    vals = []
    for delta in (-3.0, -1.5, 0.0, 1.5, 3.0):
        P.SliceGenerator = make(delta, True, thin)
        out = P.generate_puzzle(mask, params_for(Style.SLICE, "n"), "Plywood", outputs=set())
        d = kit_vs_own_body(out.model, tr.final, est, 1.0)
        vals.append((d['proj_iou_front'], d['proj_iou_top']))
        print(f"{name} thin-appendages {'ON ' if thin else 'OFF'} lattice start {delta:+.1f} mm: pieces {len(out.model.pieces)} front {d['proj_iou_front']:.3f} top {d['proj_iou_top']:.3f}")
    print(f"   -> thin {'ON ' if thin else 'OFF'}: front median {np.median([v[0] for v in vals]):.3f} (min {min(v[0] for v in vals):.3f} max {max(v[0] for v in vals):.3f}); top median {np.median([v[1] for v in vals]):.3f} (min {min(v[1] for v in vals):.3f} max {max(v[1] for v in vals):.3f})")
P.SliceGenerator = SliceGenerator
