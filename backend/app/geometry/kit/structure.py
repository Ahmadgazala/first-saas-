"""StructureAnalyzer: a generic structural model of an animal (or object) from its silhouette.

The analyzer never assumes a species. It derives, from the outline only:

* the axial body: the silhouette without limbs and large appendages;
* the spine curve: the longest path of the axial body's medial skeleton, smoothed and
  extended to the tips, from the tail end (s = 0) to the head end (s = L). Along it:
  tangent, dorsal normal, distance to the dorsal / ventral outline and estimated
  half-depth (from the pluggable depth model);
* the head (if detected), neck, torso and tail as arc-length ranges of the curve;
* limbs and large appendages (wings, fins) with their own axis polylines, attachment
  station on the spine and pairing (left/right) groups.

All confidence values are *estimated* heuristics (documented on each field), not
probabilities from a trained model.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from skimage.morphology import skeletonize

from app.geometry.anatomy import TORSO_LIKE, Anatomy, Appendage, Region, analyze
from app.geometry.depth import AnatomicalDepthModel, DepthField, DepthModel, raster_to_geometry
from app.geometry.normalize import largest_polygon

_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class SpineCurve:
    """Arc-length sampled body axis. Arrays have one row per sample."""

    pts: np.ndarray  # (N, 2) mm
    s: np.ndarray  # (N,) arc length from the tail end
    tangent: np.ndarray  # (N, 2) unit, towards the head end
    normal: np.ndarray  # (N, 2) unit, dorsal side
    dorsal: np.ndarray  # (N,) distance to the outline along +normal
    ventral: np.ndarray  # (N,) distance to the outline along -normal
    half_depth: np.ndarray  # (N,) estimated half body depth (z) at the axis
    region: np.ndarray  # (N,) Region codes

    @property
    def length(self) -> float:
        return float(self.s[-1])

    def index(self, s: float) -> int:
        return int(np.clip(np.searchsorted(self.s, s), 0, len(self.s) - 1))

    def at(self, s: float) -> dict:
        """Linearly interpolated frame at arc length s."""
        s = float(np.clip(s, 0.0, self.length))
        i = int(np.clip(np.searchsorted(self.s, s) - 1, 0, len(self.s) - 2))
        w = (s - self.s[i]) / max(self.s[i + 1] - self.s[i], 1e-9)

        def lerp(a):
            return a[i] * (1 - w) + a[i + 1] * w

        t = lerp(self.tangent)
        t = t / (np.linalg.norm(t) or 1.0)
        n = np.array([-t[1], t[0]])
        if np.dot(n, lerp(self.normal)) < 0:
            n = -n
        return {
            "point": lerp(self.pts), "tangent": t, "normal": n,
            "dorsal": float(lerp(self.dorsal)), "ventral": float(lerp(self.ventral)),
            "half_depth": float(lerp(self.half_depth)), "region": Region(int(self.region[i if w < 0.5 else i + 1])),
        }

    def project(self, xy) -> float:
        """Arc length of the curve sample nearest to a point."""
        d = np.hypot(self.pts[:, 0] - xy[0], self.pts[:, 1] - xy[1])
        return float(self.s[int(np.argmin(d))])

    def line(self, s0: float = 0.0, s1: float | None = None) -> LineString:
        s1 = self.length if s1 is None else s1
        m = (self.s >= s0) & (self.s <= s1)
        return LineString(self.pts[m]) if m.sum() >= 2 else LineString([self.at(s0)["point"], self.at(s1)["point"]])


@dataclass
class LimbStructure:
    kind: str  # "limb" | "wing" | "fin" | "appendage"
    polygon: Polygon
    axis: np.ndarray  # (M, 2) polyline from the attachment to the tip (mm)
    attach: tuple[float, float]
    attach_s: float  # station on the spine curve
    length_mm: float
    width_mm: float
    confidence: float  # estimated: elongation and attachment clarity
    group: int = -1
    side: int = 0  # +1 / -1 depth side, 0 = mirrored on both sides
    joints: np.ndarray | None = None  # (K, 2) joint centres: attachment (hip/shoulder) ... tip (toe)


@dataclass
class HeadStructure:
    polygon: Polygon
    s_start: float  # where the head begins on the spine curve
    s_end: float
    length_mm: float
    height_mm: float
    confidence: float  # estimated: size relative to the body and how distinct the blob is


@dataclass
class AnimalStructure:
    silhouette: Polygon
    anatomy: Anatomy
    depth: DepthField
    spine: SpineCurve
    head: HeadStructure | None
    limbs: list[LimbStructure]
    ranges: dict[str, tuple[float, float]]  # region name -> (s0, s1) on the spine curve
    confidence: dict[str, float]
    warnings: list[str] = field(default_factory=list)

    def range_of(self, name: str) -> tuple[float, float] | None:
        return self.ranges.get(name)

    def to_skeleton(self, n_spine: int = 12) -> dict:
        """Editable skeleton in mm: spine control points (tail -> head), where the head starts,
        and the joint chain of every limb. The same format is accepted back by the analyzer."""
        sp = self.spine
        ss = np.linspace(0.0, sp.length, n_spine)
        spine = [[round(float(v), 2) for v in sp.at(s)["point"]] for s in ss]
        head = None
        if self.head is not None:
            head = [round(float(v), 2) for v in sp.at(self.head.s_start)["point"]]
        limbs = []
        for lb in self.limbs:
            pts = lb.joints if lb.joints is not None else lb.axis[[0, -1]]
            limbs.append({"kind": lb.kind, "joints": [[round(float(x), 2), round(float(y), 2)] for x, y in pts]})
        return {"spine": spine, "head_start": head, "limbs": limbs}

    def to_dict(self) -> dict:
        """Hierarchical summary (AnimalStructure -> head / neck / spine / torso / limbs / tail)."""
        sp = self.spine

        def part(name: str, rng):
            s0, s1 = rng
            mid = sp.at(0.5 * (s0 + s1))
            return {
                "kind": name, "parent": "spine", "length_mm": round(s1 - s0, 1),
                "width_mm": round(mid["dorsal"] + mid["ventral"], 1),
                "depth_mm": round(2 * mid["half_depth"], 1),
                "position": [round(float(c), 1) for c in mid["point"]],
                "orientation_deg": round(float(np.degrees(np.arctan2(mid["tangent"][1], mid["tangent"][0]))), 1),
            }

        children = [part(n, r) for n, r in self.ranges.items() if n != "spine"]
        for i, limb in enumerate(self.limbs):
            children.append({
                "kind": limb.kind, "parent": "torso", "index": i, "length_mm": round(limb.length_mm, 1),
                "width_mm": round(limb.width_mm, 1), "group": limb.group, "side": limb.side,
                "position": [round(c, 1) for c in limb.attach], "confidence": round(limb.confidence, 2),
            })
        return {
            "spine": {"length_mm": round(sp.length, 1), "children": children},
            "has_head": self.head is not None,
            "limb_count": len(self.limbs),
            "confidence": {k: round(v, 3) for k, v in self.confidence.items()},
            "confidence_kind": "estimated",
        }


# ---------------------------------------------------------------------------- analyzer


class StructureAnalyzer:
    """Silhouette (mm, y up) -> AnimalStructure. The depth model is pluggable."""

    def __init__(self, depth_model: DepthModel | None = None, thin_appendages: bool = False):
        self.depth_model = depth_model or AnatomicalDepthModel()
        # Solid-slice style: small ventral appendages (arms, claws, small fins) become limbs too.
        self.thin_appendages = thin_appendages

    def analyze(self, silhouette: Polygon, anatomy: Anatomy | None = None, depth: DepthField | None = None,
                skeleton: dict | None = None) -> AnimalStructure:
        """`skeleton` (mm, as produced by AnimalStructure.to_skeleton and edited by the user)
        replaces the detected spine curve, head start and limb joints."""
        anatomy = anatomy or analyze(silhouette)
        depth = depth or self.depth_model.estimate(silhouette, anatomy)
        if skeleton:
            return self._from_skeleton(silhouette, anatomy, depth, skeleton)
        warnings: list[str] = []

        appendage_masks = []
        limbs: list[LimbStructure] = []
        total = float(anatomy.mask.sum()) or 1.0
        for app in anatomy.appendages:
            frac = float(app.mask.sum()) / total
            if app.kind == Region.LIMB or (app.kind == Region.EXTREMITY and frac >= 0.035):
                appendage_masks.append(app.mask)
        axial = anatomy.mask.copy()
        for m in appendage_masks:
            axial &= ~m
        for app in anatomy.appendages:
            if app.kind == Region.EXTREMITY:
                axial &= ~app.mask  # ears, horns, small fins are not part of the axis
        axial &= anatomy.labels != Region.EXTREMITY  # ears / horns / antlers inside the head region
        if axial.sum() < 0.25 * total:
            axial = anatomy.mask.copy()

        spine = self._spine(silhouette, anatomy, depth, axial)
        ranges = _ranges(spine)

        for app in anatomy.appendages:
            frac = float(app.mask.sum()) / total
            if app.kind == Region.LIMB:
                kind = "limb"
            elif app.kind == Region.EXTREMITY and frac >= 0.035:
                kind = _appendage_kind(app, spine)
            else:
                continue
            limb = _limb(app, anatomy, silhouette, spine, kind)
            if limb is None:
                continue
            tail = ranges.get("tail")
            if tail is not None and limb.attach_s < tail[1] - 0.02 * spine.length and "torso" in ranges:
                continue  # a hanging tail reaching the ground is still the tail, not a leg
            limbs.append(limb)
        if self.thin_appendages:
            limbs += _thin_appendage_limbs(anatomy, silhouette, spine, ranges, limbs, total)
        _group_limbs(limbs, spine)

        head = None
        if "head" in ranges:
            s0, s1 = ranges["head"]
            hpoly = _head_polygon(silhouette, anatomy, spine, s0)
            if hpoly is not None and hpoly.area > 0:
                rect = np.asarray(hpoly.minimum_rotated_rectangle.exterior.coords)
                sides = sorted(float(np.hypot(*(rect[i + 1] - rect[i]))) for i in range(4))
                height = sides[0]
                rel = hpoly.area / silhouette.area
                conf = float(np.clip(0.55 + 4.0 * min(rel, 0.12), 0.0, 0.98))
                length = float(max(np.ptp(np.asarray(hpoly.exterior.coords) @ spine.at(s1)["tangent"]), s1 - s0))
                head = HeadStructure(hpoly, s0, s1, length, height, conf)

        if head is None and "tail" in ranges and anatomy.head_side != 0:
            # A big head merged into the torso blob (dinosaurs, lions, elephants): the tail identifies the
            # other end, and the head is the bulge beyond the neck constriction of the width profile.
            s_neck = _neck_constriction(spine, ranges)
            hpoly = _beyond(silhouette, spine, s_neck) if s_neck is not None else None
            if hpoly is not None and hpoly.area > 0:
                rect = np.asarray(hpoly.minimum_rotated_rectangle.exterior.coords)
                sides = sorted(float(np.hypot(*(rect[i + 1] - rect[i]))) for i in range(4))
                head = HeadStructure(hpoly, s_neck, spine.length, sides[-1], sides[0], 0.6)
                ranges["head"] = (s_neck, spine.length)
                ranges["torso"] = (ranges["torso"][0], min(ranges["torso"][1], s_neck))
                limbs = [lb for lb in limbs if lb.attach_s < s_neck - 0.03 * spine.length]  # a jaw is not a limb
                warnings.append("The head was found as the bulge beyond the neck in the width profile.")

        if head is not None:
            spine, ranges, head = self._aim_head(silhouette, anatomy, depth, spine, ranges, head)

        conf = _confidence(silhouette, anatomy, spine, head, limbs)
        if head is None and anatomy.head_side == 0:
            warnings.append("No head was detected: the kit is built as a generic structure along the main axis.")
        return AnimalStructure(silhouette, anatomy, depth, spine, head, limbs, ranges, conf, warnings)

    def _aim_head(self, silhouette, anatomy, depth, spine: SpineCurve, ranges, head: HeadStructure):
        """The medial axis of a head often runs out into an ear or a horn. Inside the head, the
        axis is replaced by a smooth path from the head start to the head point farthest from
        it (snout / beak tip), stopping a little inside the outline."""
        s0 = head.s_start
        start = spine.at(s0)["point"]
        # Target: the skull blob (head label without ears / horns / an open jaw), else the head.
        m = anatomy.region_mask((Region.HEAD,))
        blob = raster_to_geometry(m, anatomy.x0, anatomy.y0, anatomy.res) if m.any() else None
        blob = largest_polygon(make_valid(blob.intersection(head.polygon.buffer(1.0)))) if blob is not None else None
        target = blob if blob is not None and blob.area > 0.3 * head.polygon.area else head.polygon
        coords = np.asarray(target.exterior.coords)
        tip = coords[int(np.argmax(np.hypot(coords[:, 0] - start[0], coords[:, 1] - start[1])))]
        d = tip - start
        L = float(np.linalg.norm(d))
        if L < 1e-6:
            return spine, ranges, head
        tip = start + d * (1.0 - min(0.12, 4.0 / L))
        keep = spine.pts[spine.s <= s0]
        t = np.linspace(0.0, 1.0, 30)[1:, None]
        tan = spine.at(s0)["tangent"]
        ctrl = start + tan * 0.35 * L
        path = (1 - t) ** 2 * start + 2 * (1 - t) * t * ctrl + t ** 2 * tip
        new = self._curve(silhouette, anatomy, depth, np.vstack([keep, path]), orient=False)
        if new.length < 0.5 * spine.length:
            return spine, ranges, head
        s_new = new.project(start)
        ranges = {k: v for k, v in ranges.items() if k not in ("spine", "head")}
        ranges = {k: (a, min(b, s_new)) for k, (a, b) in ranges.items() if a < s_new}
        ranges["spine"] = (0.0, new.length)
        ranges["head"] = (s_new, new.length)
        head = HeadStructure(head.polygon, s_new, new.length, head.length_mm, head.height_mm, head.confidence)
        return new, ranges, head

    # ------------------------------------------------------------------ user-edited skeleton

    def _from_skeleton(self, silhouette: Polygon, anatomy: Anatomy, depth: DepthField, sk: dict) -> AnimalStructure:
        from app.core.errors import ErrorCode, PuzzleError

        pts = np.asarray(sk.get("spine") or [], dtype=float)
        if pts.ndim != 2 or len(pts) < 2:
            raise PuzzleError(ErrorCode.INVALID_SETTINGS, "The edited skeleton needs a spine with at least two points.")
        pts = _smooth_polyline(pts)
        spine = self._curve(silhouette, anatomy, depth, pts, orient=False)
        ranges = _ranges(spine)
        head = None
        hs = sk.get("head_start")
        if hs is not None:
            s0 = spine.project(hs)
            if spine.length - s0 > 0.03 * spine.length:
                for name in ("neck", "torso", "tail"):
                    if name in ranges:
                        a, b = ranges[name]
                        if a >= s0:
                            del ranges[name]
                        elif b > s0:
                            ranges[name] = (a, s0)
                ranges["head"] = (s0, spine.length)
                if "torso" not in ranges:
                    ranges["torso"] = (ranges.get("tail", (0.0, 0.0))[1], s0)
                hpoly = _beyond(silhouette, spine, s0)
                if hpoly is not None:
                    rect = np.asarray(hpoly.minimum_rotated_rectangle.exterior.coords)
                    sides = sorted(float(np.hypot(*(rect[i + 1] - rect[i]))) for i in range(4))
                    head = HeadStructure(hpoly, s0, spine.length, sides[-1], sides[0], 0.95)
        else:
            ranges.pop("head", None)
            if "neck" in ranges:
                ranges["torso"] = (ranges["torso"][0], ranges["neck"][1])
                del ranges["neck"]
        limbs = []
        for lb in sk.get("limbs") or []:
            joints = np.asarray(lb.get("joints") or [], dtype=float)
            if joints.ndim != 2 or len(joints) < 2:
                continue
            limb = _limb_from_joints(joints, silhouette, spine, str(lb.get("kind") or "limb"))
            if limb is not None:
                limbs.append(limb)
        _group_limbs(limbs, spine)
        conf = _confidence(silhouette, anatomy, spine, head, limbs)
        conf["structure"] = max(conf["structure"], 0.9)  # confirmed by the user
        return AnimalStructure(silhouette, anatomy, depth, spine, head, limbs, ranges, conf,
                               ["The skeleton was edited by hand."])

    # ------------------------------------------------------------------ spine curve

    def _spine(self, silhouette: Polygon, anatomy: Anatomy, depth: DepthField, axial: np.ndarray) -> SpineCurve:
        res = anatomy.res
        path = _longest_path(skeletonize(axial))  # spurs never lie on the longest path
        if len(path) < 3:
            ys, xs = np.nonzero(axial)
            pts = np.stack([anatomy.x0 + xs * res, anatomy.y0 + ys * res], 1)
            c = pts.mean(0)
            _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
            span = np.abs((pts - c) @ vt[0]).max()
            path_mm = np.array([c - vt[0] * span, c + vt[0] * span])
        else:
            rc = np.asarray(path, dtype=np.float64)
            path_mm = np.stack([anatomy.x0 + rc[:, 1] * res, anatomy.y0 + rc[:, 0] * res], 1)

        pts = _resample(path_mm, max(res, 0.5))
        if len(pts) >= 7:
            k = max(3, int(0.06 * len(pts)) | 1)
            pad = np.vstack([np.repeat(pts[:1], k, 0), pts, np.repeat(pts[-1:], k, 0)])
            kern = np.ones(k) / k
            sm = np.stack([np.convolve(pad[:, i], kern, mode="same") for i in range(2)], 1)[k:-k]
            sm[0], sm[-1] = pts[0], pts[-1]
            pts = sm

        # Extend both ends towards the outline (the skeleton stops one radius short of the tips).
        boundary = silhouette.boundary
        for end in (0, -1):
            p = pts[end]
            q = pts[min(4, len(pts) - 1)] if end == 0 else pts[max(-5, -len(pts))]
            d = p - q
            nd = np.linalg.norm(d)
            if nd < 1e-9:
                continue
            d = d / nd
            reach = _ray(boundary, p, d)
            if reach > 0:
                ext = p + d * 0.8 * reach
                pts = np.vstack([ext, pts]) if end == 0 else np.vstack([pts, ext])

        return self._curve(silhouette, anatomy, depth, pts, orient=True)

    def _curve(self, silhouette: Polygon, anatomy: Anatomy, depth: DepthField, pts: np.ndarray,
               orient: bool) -> SpineCurve:
        """Arc-length sampled curve with frames, outline distances, depth and regions.
        `orient` = detect which end is the head (otherwise the points are tail -> head)."""
        boundary = silhouette.boundary
        length = float(np.sum(np.hypot(*np.diff(pts, axis=0).T)))
        pts = _resample(pts, max(0.5, length / 400.0))
        tangent = np.gradient(pts, axis=0)
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-9)
        normal = np.stack([-tangent[:, 1], tangent[:, 0]], 1)

        region = np.array([int(anatomy.region_at(*p)) for p in pts])
        torso = np.isin(region, [int(r) for r in TORSO_LIKE])
        ref = normal[torso] if torso.any() else normal
        if ref[:, 1].mean() < 0:
            normal = -normal

        # Orientation: s = 0 at the tail end, s = L at the head end.
        head_end = _head_end(pts, region, anatomy) if orient else -1
        if head_end == 0:
            pts, tangent, normal, region = pts[::-1], -tangent[::-1], normal[::-1], region[::-1]
        dorsal = np.array([_ray(boundary, p, n) for p, n in zip(pts, normal)])
        ventral = np.array([_ray(boundary, p, -n) for p, n in zip(pts, normal)])
        half_depth = np.maximum(depth.sample(pts[:, 0], pts[:, 1]), 0.0)
        s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        # Background samples (curve slightly outside the outline near tips): nearest valid region.
        if (region == 0).any() and (region != 0).any():
            valid = np.nonzero(region != 0)[0]
            for i in np.nonzero(region == 0)[0]:
                region[i] = region[valid[np.argmin(np.abs(valid - i))]]
        return SpineCurve(pts, s, tangent, normal, dorsal, ventral, half_depth, region)


# ---------------------------------------------------------------------------- helpers


def _head_polygon(silhouette: Polygon, anatomy: Anatomy, spine: SpineCurve, s0: float) -> Polygon | None:
    """Whole head: the skull blob plus the parts in front of the back of the skull that lie
    close to it (an open lower jaw, a beak), with small openings closed (the kit cuts its own
    mouth). Falls back to the silhouette past the neck cut."""
    m = anatomy.region_mask((Region.HEAD,))
    skull = raster_to_geometry(m, anatomy.x0, anatomy.y0, anatomy.res) if m.any() else None
    skull = largest_polygon(make_valid(skull.buffer(0.75 * anatomy.res).intersection(silhouette))) if skull else None
    if skull is None or skull.area <= 0:
        return _beyond(silhouette, spine, s0)
    pts = np.asarray(skull.exterior.coords)
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    e1 = vt[0]
    if np.dot(c - spine.at(s0)["point"], e1) < 0:
        e1 = -e1
    proj = (pts - c) @ e1
    back, length = float(proj.min()), float(proj.max() - proj.min())
    e2 = np.array([-e1[1], e1[0]])
    origin = c + e1 * back
    half = Polygon([tuple(origin + e2 * 1e4), tuple(origin + e2 * 1e4 + e1 * 1e4),
                    tuple(origin - e2 * 1e4 + e1 * 1e4), tuple(origin - e2 * 1e4)])
    zone = make_valid(silhouette.intersection(half).intersection(skull.buffer(0.35 * length)))
    near = [g for g in getattr(zone, "geoms", [zone]) if g.geom_type == "Polygon"]
    r = 0.12 * length
    head = make_valid(unary_union([skull, *near]).buffer(r).buffer(-r).intersection(half))
    head = largest_polygon(head)
    return head if head is not None and head.area >= skull.area else skull


def _thin_appendage_limbs(anatomy: Anatomy, silhouette: Polygon, spine: SpineCurve, ranges: dict,
                          limbs: list[LimbStructure], total_px: float, max_clusters: int = 2) -> list[LimbStructure]:
    """Small appendages that are too small to count as limbs by area (arms, claws, small fins) but are
    large enough to cut: elongated, thick enough for a plate, not pointing to the dorsal side (those are
    crests and spikes), attached along the body, away from the head and from existing limbs. Fragments
    of one arm (separate claws) are clustered by attachment distance and merged into one plate outline."""
    import cv2

    min_feature = 2.5
    cand = []
    head_s = ranges.get("head", (spine.length, spine.length))[0]
    for app in anatomy.appendages:
        if app.kind != Region.EXTREMITY:
            continue
        frac = float(app.mask.sum()) / total_px
        if frac < 0.0015 or frac >= 0.035:  # bigger ones are limbs already
            continue
        s = spine.project(app.attach)
        if _appendage_kind(app, spine) == "wing" or s > head_s - 0.02 * spine.length:
            continue
        length = float(np.hypot(app.tip[0] - app.attach[0], app.tip[1] - app.attach[1]))
        if app.thickness_mm < 1.6 * min_feature or length < 2.5 * min_feature or length < 1.2 * app.thickness_mm:
            continue
        if any(np.hypot(app.attach[0] - lb.attach[0], app.attach[1] - lb.attach[1]) < 0.5 * lb.width_mm + 8.0 for lb in limbs):
            continue
        cand.append((app, s))
    clusters: list[list] = []
    link = 0.07 * spine.length
    for app, s in sorted(cand, key=lambda t: t[1]):
        for cl in clusters:
            if any(np.hypot(app.attach[0] - o.attach[0], app.attach[1] - o.attach[1]) < link for o, _ in cl):
                cl.append((app, s))
                break
        else:
            clusters.append([(app, s)])
    clusters.sort(key=lambda cl: -sum(float(a.mask.sum()) for a, _ in cl))
    out = []
    for cl in clusters[:max_clusters]:
        mask = np.zeros_like(cl[0][0].mask)
        for a, _ in cl:
            mask |= a.mask
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(round(4.0 / anatomy.res)) + 1,) * 2)) > 0
        attach = np.mean([a.attach for a, _ in cl], axis=0)
        tip = max((a.tip for a, _ in cl), key=lambda t: np.hypot(t[0] - attach[0], t[1] - attach[1]))
        merged = Appendage(Region.EXTREMITY, mask, (float(attach[0]), float(attach[1])), (float(tip[0]), float(tip[1])),
                           max(a.thickness_mm for a, _ in cl))
        lb = _limb(merged, anatomy, silhouette, spine, "appendage")
        if lb is not None and lb.length_mm >= 2.5 * min_feature and lb.polygon.area >= 6.0 * min_feature ** 2:
            out.append(lb)
    return out


def _neck_constriction(spine: SpineCurve, ranges: dict) -> float | None:
    """Arc length of the neck: the narrowest point of the width profile in the head-end part of the
    spine that is followed by a clearly wider bulge (the skull) and preceded by a wider torso."""
    s, L = spine.s, spine.length
    H = spine.dorsal + spine.ventral
    k = max(3, int(0.03 * len(s)) | 1)
    Hs = np.convolve(np.pad(H, k // 2, mode="edge"), np.ones(k) / k, mode="valid")
    w = max(2, int(0.04 * len(s)))
    t0, t1 = ranges.get("torso", (0.0, L))
    torso_h = float(Hs[(s >= t0) & (s <= t1)].max()) if ((s >= t0) & (s <= t1)).any() else float(Hs.max())
    best, best_ratio = None, 1.3
    for i in range(w, len(s) - w):
        if not (0.55 * L <= s[i] <= 0.93 * L) or Hs[i] > Hs[i - w:i + w + 1].min() + 1e-9:
            continue
        head_len = L - s[i]
        bulge = float(Hs[i:].max())
        ratio = bulge / max(Hs[i], 1e-6)
        if 0.10 * L <= head_len <= 0.45 * L and torso_h >= 1.2 * Hs[i] and ratio > best_ratio:
            best, best_ratio = float(s[i]), ratio
    return best


def _beyond(silhouette: Polygon, spine: SpineCurve, s0: float) -> Polygon | None:
    """Part of the silhouette past the cut perpendicular to the axis at s0 that contains the
    axis end (the whole head with jaw, horns, ears)."""
    from shapely.ops import split

    f = spine.at(s0)
    p, n, t = f["point"], f["normal"], f["tangent"]
    line = LineString([tuple(p - n * 1e4), tuple(p + n * 1e4)])
    end = Point(spine.pts[-1])
    best = None
    for g in getattr(split(silhouette, line), "geoms", []):
        if g.geom_type != "Polygon":
            continue
        c = np.asarray(g.representative_point().coords[0])
        if np.dot(c - p, t) <= 0:
            continue
        if best is None or g.distance(end) < best.distance(end) - 1e-9:
            best = g
    return best


def _ray(boundary, p, d, far: float = 1e4) -> float:
    hit = boundary.intersection(LineString([tuple(p), tuple(p + d * far)]))
    if hit.is_empty:
        return 0.0
    origin = Point(p)
    geoms = getattr(hit, "geoms", [hit])
    dists = [origin.distance(g) for g in geoms if not g.is_empty]
    return float(min(dists)) if dists else 0.0


def _resample(pts: np.ndarray, step: float) -> np.ndarray:
    seg = np.hypot(*np.diff(pts, axis=0).T)
    keep = np.concatenate([[True], seg > 1e-9])
    pts = pts[keep]
    if len(pts) < 2:
        return pts
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    n = max(2, int(np.ceil(s[-1] / step)) + 1)
    t = np.linspace(0.0, s[-1], n)
    return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], 1)


def _neighbours(skel: np.ndarray, r: int, c: int):
    h, w = skel.shape
    for dr, dc in _OFFSETS:
        rr, cc = r + dr, c + dc
        if 0 <= rr < h and 0 <= cc < w and skel[rr, cc]:
            yield rr, cc


def _bfs(skel: np.ndarray, start: tuple[int, int]):
    prev = {start: None}
    q = deque([start])
    last = start
    while q:
        cur = q.popleft()
        last = cur
        for nb in _neighbours(skel, *cur):
            if nb not in prev:
                prev[nb] = cur
                q.append(nb)
    return last, prev


def _longest_path(skel: np.ndarray) -> list[tuple[int, int]]:
    """Approximate longest path of a skeleton graph (double BFS on its largest component)."""
    pts = np.argwhere(skel)
    if len(pts) == 0:
        return []
    # Largest connected component first.
    seen: set = set()
    best: list = []
    for r, c in map(tuple, pts):
        if (r, c) in seen:
            continue
        _, prev = _bfs(skel, (r, c))
        seen.update(prev)
        if len(prev) > len(best):
            best = list(prev)
    a, _ = _bfs(skel, best[0])
    b, prev = _bfs(skel, a)
    path = []
    cur = b
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return path


def _head_end(pts: np.ndarray, region: np.ndarray, anatomy: Anatomy) -> int:
    n = len(pts)
    k = max(1, n // 6)
    head = int(Region.HEAD), int(Region.NECK)
    a = np.isin(region[:k], head).mean()
    b = np.isin(region[-k:], head).mean()
    if a != b:
        return 0 if a > b else -1
    tail = int(Region.TAIL)
    a, b = (region[:k] == tail).mean(), (region[-k:] == tail).mean()
    if a != b:
        return -1 if a > b else 0
    if anatomy.head_side:
        return -1 if (pts[-1, 0] - pts[0, 0]) * anatomy.head_side >= 0 else 0
    return -1


def _ranges(spine: SpineCurve) -> dict[str, tuple[float, float]]:
    """Contiguous arc-length ranges of head (at the end), neck, tail (at the start), torso."""
    reg = spine.region
    s = spine.s
    n = len(s)
    out: dict[str, tuple[float, float]] = {"spine": (0.0, spine.length)}
    i = n - 1
    while i > 0 and reg[i] in (Region.HEAD, Region.EXTREMITY):
        i -= 1
    head_start = i + 1
    if head_start < n - 1 and (s[-1] - s[min(head_start, n - 1)]) > 0.04 * spine.length:
        out["head"] = (float(s[head_start]), float(s[-1]))
    else:
        head_start = n
    j = head_start - 1
    while j > 0 and reg[j] == Region.NECK:
        j -= 1
    if head_start - 1 - j > 2:
        out["neck"] = (float(s[j + 1]), float(s[min(head_start, n - 1)] if head_start < n else s[-1]))
    torso_end = j + 1
    k = 0
    while k < n - 1 and reg[k] == Region.TAIL:
        k += 1
    if k > 2:
        out["tail"] = (0.0, float(s[k]))
    torso_start = k if k > 2 else 0
    if torso_end - torso_start > 2:
        out["torso"] = (float(s[torso_start]), float(s[min(torso_end, n - 1)]))
    else:
        out["torso"] = (0.0, spine.length)
    return out


def _appendage_kind(app, spine: SpineCurve) -> str:
    vx, vy = app.tip[0] - app.attach[0], app.tip[1] - app.attach[1]
    n = np.hypot(vx, vy) or 1.0
    s = spine.project(app.attach)
    nrm = spine.at(s)["normal"]
    up = (vx * nrm[0] + vy * nrm[1]) / n
    if up > 0.4:
        return "wing"
    if up < -0.4:
        return "fin"
    return "appendage"


def _limb(app, anatomy: Anatomy, silhouette: Polygon, spine: SpineCurve, kind: str) -> LimbStructure | None:
    poly = raster_to_geometry(app.mask, anatomy.x0, anatomy.y0, anatomy.res)
    if poly is None:
        return None
    poly = largest_polygon(make_valid(poly.buffer(0.75 * anatomy.res).intersection(silhouette)))
    if poly is None or poly.area <= 0:
        return None
    sk = skeletonize(app.mask)
    path = _longest_path(sk)
    attach = np.array(app.attach)
    tip = np.array(app.tip)
    if len(path) >= 3:
        rc = np.asarray(path, dtype=np.float64)
        axis = np.stack([anatomy.x0 + rc[:, 1] * anatomy.res, anatomy.y0 + rc[:, 0] * anatomy.res], 1)
        if np.linalg.norm(axis[0] - attach) > np.linalg.norm(axis[-1] - attach):
            axis = axis[::-1]
        axis = np.vstack([attach, axis[1:-1], tip]) if len(axis) > 2 else np.vstack([attach, tip])
    else:
        axis = np.vstack([attach, tip])
    axis = _resample(axis, max(1.0, anatomy.res))
    length = float(np.sum(np.hypot(*np.diff(axis, axis=0).T)))
    width = app.thickness_mm
    elong = length / max(width, 1e-6)
    conf = float(np.clip(0.4 + 0.15 * elong, 0.0, 0.95))
    return LimbStructure(kind, poly, axis, (float(attach[0]), float(attach[1])), spine.project(attach),
                         length, width, conf, joints=_limb_joints(axis))


def _limb_joints(axis: np.ndarray) -> np.ndarray:
    """Hip / knee / ankle / toe along a limb axis: knee and ankle at the strongest bends in
    the usual ranges of the length (fallback: 45 % and 80 %)."""
    seg = np.hypot(*np.diff(axis, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = s[-1]
    if L <= 0 or len(axis) < 5:
        return np.vstack([axis[0], axis[-1]])
    step = max(2, len(axis) // 12)
    ang = np.zeros(len(axis))
    for i in range(step, len(axis) - step):
        a, b = axis[i] - axis[i - step], axis[i + step] - axis[i]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na > 0 and nb > 0:
            ang[i] = np.arccos(np.clip(np.dot(a, b) / (na * nb), -1, 1))

    def pick(lo, hi, default):
        m = (s >= lo * L) & (s <= hi * L)
        if m.any() and ang[m].max() > 0.25:
            idx = np.nonzero(m)[0][int(np.argmax(ang[m]))]
            return axis[idx]
        return np.array([np.interp(default * L, s, axis[:, 0]), np.interp(default * L, s, axis[:, 1])])

    knee = pick(0.3, 0.6, 0.45)
    ankle = pick(0.65, 0.9, 0.8)
    return np.vstack([axis[0], knee, ankle, axis[-1]])


def _limb_from_joints(joints: np.ndarray, silhouette: Polygon, spine: SpineCurve, kind: str) -> LimbStructure | None:
    """Limb from a user-placed joint chain: its outline is the silhouette around the chain."""
    from app.geometry.normalize import polygon_parts

    line = LineString(joints)
    if line.length <= 0:
        return None
    boundary = silhouette.boundary
    widths = [2.0 * boundary.distance(Point(p)) for p in joints if silhouette.contains(Point(p))]
    w = float(np.median(widths)) if widths else 0.08 * line.length
    w = float(np.clip(w, 0.04 * line.length, 0.35 * line.length))
    region = make_valid(silhouette.intersection(line.buffer(0.8 * w)))
    parts = [g for g in polygon_parts(region)]
    poly = max(parts, key=lambda g: g.intersection(line.buffer(1.0)).area) if parts else line.buffer(0.5 * w)
    axis = _resample(np.asarray(line.coords), 1.0)
    attach = tuple(float(v) for v in joints[0])
    return LimbStructure(kind, poly, axis, attach, spine.project(attach), float(line.length), w, 0.95,
                         joints=np.asarray(joints, dtype=float))


def _smooth_polyline(pts: np.ndarray) -> np.ndarray:
    """Catmull-Rom style smoothing through user control points (endpoints kept)."""
    if len(pts) < 3:
        return _resample(pts, 1.0)
    from scipy.interpolate import splev, splprep

    keep = np.concatenate([[True], np.hypot(*np.diff(pts, axis=0).T) > 1e-6])
    pts = pts[keep]
    if len(pts) < 3:
        return _resample(pts, 1.0)
    k = min(3, len(pts) - 1)
    tck, _ = splprep([pts[:, 0], pts[:, 1]], s=0, k=k)
    u = np.linspace(0, 1, max(50, 20 * len(pts)))
    x, y = splev(u, tck)
    return np.stack([x, y], 1)


def _group_limbs(limbs: list[LimbStructure], spine: SpineCurve) -> None:
    """Pair limbs attached close together on the spine (left / right of one girdle)."""
    order = sorted(range(len(limbs)), key=lambda i: limbs[i].attach_s)
    gap = 0.16 * spine.length
    group = -1
    last_s = None
    members: dict[int, list[int]] = {}
    for i in order:
        lb = limbs[i]
        if last_s is None or lb.attach_s - last_s > gap or len(members[group]) >= 2 or (
            members[group] and limbs[members[group][0]].kind != lb.kind
        ):
            group += 1
            members[group] = []
        members[group].append(i)
        lb.group = group
        last_s = lb.attach_s
    for idx in members.values():
        if len(idx) == 1:
            limbs[idx[0]].side = 0  # one visible limb stands for a mirrored pair
        else:
            for k, i in enumerate(idx):
                limbs[i].side = 1 if k == 0 else -1


def _confidence(silhouette, anatomy, spine, head, limbs) -> dict[str, float]:
    """Estimated (heuristic) confidences in [0, 1]:

    spine     share of axis samples with outline on both sides and a smooth heading
    head      size of the head blob relative to the body (0 when no head)
    limbs     mean limb elongation score (0.5 when the shape has no limbs)
    structure weighted mean of the three
    """
    inside = ((spine.dorsal > 0) & (spine.ventral > 0)).mean()
    turn = np.abs(np.diff(np.unwrap(np.arctan2(spine.tangent[:, 1], spine.tangent[:, 0]))))
    smooth = float(np.clip(1.0 - np.percentile(turn, 95) / 0.5, 0.0, 1.0)) if len(turn) else 0.0
    sp = float(np.clip(0.6 * inside + 0.4 * smooth, 0.0, 1.0))
    hd = head.confidence if head else 0.0
    lb = float(np.mean([lm.confidence for lm in limbs])) if limbs else 0.5
    structure = 0.5 * sp + 0.25 * (hd if head else 0.5) + 0.25 * lb
    return {"spine": sp, "head": hd, "limbs": lb, "structure": float(structure)}


class ImageFrame:
    """Maps between the photo (fractions of width / height, y down - what the skeleton editor
    uses) and the normalised silhouette in mm (y up). Both describe the same outline, so the
    bounding box of the mask's main component maps onto the polygon's bounding box."""

    def __init__(self, mask: np.ndarray, polygon: Polygon):
        import cv2

        m = (mask > 127).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 1:
            raise ValueError("empty mask")
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y, w, h = (int(v) for v in stats[i, :4])
        self.h, self.w = mask.shape[:2]
        self.px = (x, y, x + w, y + h)
        minx, miny, maxx, maxy = polygon.bounds
        self.mm = (minx, miny, maxx, maxy)
        self.scale = max(w, h) / max(maxx - minx, maxy - miny)  # px per mm

    def to_fraction(self, p) -> list[float]:
        x0, _, _, y1 = self.px
        minx, miny, _, _ = self.mm
        px = x0 + (p[0] - minx) * self.scale
        py = y1 - (p[1] - miny) * self.scale
        return [round(px / self.w, 5), round(py / self.h, 5)]

    def to_mm(self, f) -> list[float]:
        x0, _, _, y1 = self.px
        minx, miny, _, _ = self.mm
        px, py = f[0] * self.w, f[1] * self.h
        return [minx + (px - x0) / self.scale, miny + (y1 - py) / self.scale]

    def skeleton_to_fractions(self, sk: dict) -> dict:
        return self._map(sk, self.to_fraction)

    def skeleton_to_mm(self, sk: dict) -> dict:
        return self._map(sk, self.to_mm)

    @staticmethod
    def _map(sk: dict, fn) -> dict:
        return {
            "spine": [fn(p) for p in sk.get("spine") or []],
            "head_start": fn(sk["head_start"]) if sk.get("head_start") is not None else None,
            "limbs": [{"kind": lb.get("kind", "limb"), "joints": [fn(p) for p in lb.get("joints") or []]}
                      for lb in sk.get("limbs") or []],
        }
