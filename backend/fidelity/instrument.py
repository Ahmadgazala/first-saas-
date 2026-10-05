"""Read-only instrumentation of the production generators (harness process only).

InstrumentedKit subclasses app.geometry.kit.builder.KitGenerator and only *observes*: every
override calls the production method unchanged and records what happened, so the generated
model is identical (verified by fidelity/tests). Recorded:

  candidates   every cross piece offered to `_try_cross` (role, station, local polygon before
               slots, frame, accepted?) grouped by the `_try_rib` / `_try_vertebra` /
               girdle / skull-spacer call that produced it
  dropped      plates removed by `_drop_plate` (limb chains of a failed girdle, skull/jaw)
  budget       (ribs, vertebrae) requested by `_budget`
  trimmed      share of the spine axis cut off as too thin (s_lo / s_hi)
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from app.geometry.kit.builder import KitGenerator

RECORDERS: list["InstrumentedKit"] = []


@dataclass
class Group:
    kind: str  # rib | vertebra | girdle | skull_spacer
    station: float | None
    candidates: list[dict] = field(default_factory=list)
    accepted: bool = False


class InstrumentedKit(KitGenerator):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.groups: list[Group] = []
        self.dropped: list[dict] = []
        self.budget_req: tuple[int, int] | None = None
        self.model = None
        self._group: Group | None = None
        RECORDERS.append(self)

    # --- grouping wrappers -------------------------------------------------------

    def _run_group(self, kind: str, station, fn, *args):
        prev = self._group
        self._group = Group(kind, station)
        try:
            ok = fn(*args)
        finally:
            g = self._group
            self._group = prev
        g.accepted = bool(ok) if kind in ("rib", "vertebra") else any(c["accepted"] for c in g.candidates)
        self.groups.append(g)
        return ok

    def _try_rib(self, s, rp):
        return self._run_group("rib", float(s), super()._try_rib, s, rp)

    def _try_vertebra(self, s):
        return self._run_group("vertebra", float(s), super()._try_vertebra, s)

    def _place_girdle(self, g):
        return self._run_group("girdle", float(g["s"]), super()._place_girdle, g)

    def _skull_spacers(self):
        return self._run_group("skull_spacer", None, super()._skull_spacers)

    # --- observers ---------------------------------------------------------------

    def _try_cross(self, part):
        poly0 = copy.deepcopy(part.poly)
        ok = super()._try_cross(part)
        if self._group is not None:
            self._group.candidates.append({
                "role": part.role.value, "station": float(part.station), "poly": poly0, "frame": part.frame,
                "accepted": bool(ok), "area": float(poly0.area),
            })
        return ok

    def _drop_plate(self, key):
        pl = self.plates.get(key)
        if pl is not None:
            self.dropped.append({"role": pl.role.value, "z": pl.z, "poly": copy.deepcopy(pl.poly), "key": key})
        return super()._drop_plate(key)

    def _budget(self, n_ribs_max):
        r = super()._budget(n_ribs_max)
        self.budget_req = r
        return r

    def build(self, st, volume=None):
        model = super().build(st, volume=volume)
        self.model = model
        return model

    # --- summary -----------------------------------------------------------------

    def degradation(self) -> dict:
        st = self.st
        L = st.spine.length
        out: dict = {
            "trimmed_axis_fraction": float((self.s_lo + (L - self.s_hi)) / L) if L > 0 else 0.0,
            "ribs_requested": self.budget_req[0] if self.budget_req else None,
            "vertebrae_requested": self.budget_req[1] if self.budget_req else None,
            "dropped_plates": [d["role"] for d in self.dropped if d["role"] != "connector"],
        }
        for kind in ("rib", "vertebra", "girdle", "skull_spacer"):
            gs = [g for g in self.groups if g.kind == kind]
            first_ok = sum(1 for g in gs if g.candidates and g.candidates[0]["accepted"])
            shrunk = [g for g in gs if g.accepted and g.candidates and not g.candidates[0]["accepted"]]
            failed = [g for g in gs if not g.accepted]
            ratios = []
            for g in shrunk:
                acc = next(c for c in g.candidates if c["accepted"])
                ratios.append(acc["area"] / g.candidates[0]["area"] if g.candidates[0]["area"] > 0 else 1.0)
            out[f"{kind}_attempts"] = len(gs)
            out[f"{kind}_first_try"] = first_ok
            out[f"{kind}_shrunk"] = len(shrunk)
            out[f"{kind}_failed_attempts"] = len(failed)
            out[f"{kind}_shrink_area_ratio_mean"] = float(sum(ratios) / len(ratios)) if ratios else None
        if self.model is not None:
            roles: dict[str, int] = {}
            for pc in self.model.pieces:
                roles[pc.role.value] = roles.get(pc.role.value, 0) + 1
            out["ribs_placed"] = roles.get("rib", 0)
            out["vertebrae_placed"] = roles.get("vertebra", 0)
            out["warnings"] = [w for w in self.model.warnings if "label" not in w]
        return out

    def intended_extra(self):
        """Geometry the generator *wanted* but did not keep: the first (full-size) candidate of
        every shrunk or failed group, and every dropped plate. Returns (cross_items, plates)."""
        crosses = []
        for g in self.groups:
            if not g.candidates or g.candidates[0]["accepted"]:
                continue
            c = g.candidates[0]
            crosses.append((c["poly"], c["frame"], c["role"]))
        plates = [(d["poly"], d["z"], d["role"]) for d in self.dropped]
        return crosses, plates


ENGINE_LOG: list[dict] = []


def instrument_engine():
    """Wrap PuzzleGenerator._build (egg-crate) to log rejected plate configurations."""
    from app.geometry.engine import ConfigurationFailed, PuzzleGenerator

    if getattr(PuzzleGenerator, "_fidelity_wrapped", False):
        return
    original = PuzzleGenerator._build

    def _build(self, *args, **kw):
        try:
            return original(self, *args, **kw)
        except ConfigurationFailed as exc:
            ENGINE_LOG.append({"code": str(exc.code), "message": exc.message, "plan_size": len(args[-2]) if len(args) >= 2 else None})
            raise

    PuzzleGenerator._build = _build
    PuzzleGenerator._fidelity_wrapped = True
