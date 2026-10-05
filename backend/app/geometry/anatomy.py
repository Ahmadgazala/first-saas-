"""Structural analysis of a silhouette: regions, medial skeleton, local axes and curvature.

The analysis turns a flat outline into the information a puzzle designer would use:

* Regions. The torso is the largest "blob" (morphological opening with a radius
  proportional to the thickest part). Everything that protrudes from it is an appendage
  and is classified by where it attaches and where it points:
      LIMB       attached to the lower torso and pointing down (legs)
      HEAD/NECK  the dominant appendage at one end of the torso; its distal blob is the
                 head, the proximal part the neck, thin protrusions of the head are
                 EXTREMITY (ears, horns, antlers)
      TAIL       thin appendage at the opposite end
      EXTREMITY  anything else (trunk, wings, fins, horns)
  The torso is subdivided into CHEST (head side) and BELLY (lower part).
  Shapes without appendages (logos, objects) are simply one TORSO region.
* A pruned medial skeleton with a local axis direction at every skeleton pixel, used to
  orient cross ribs perpendicular to necks, tails and legs.
* Boundary curvature, used to concentrate ribs where the outline changes quickly.

Everything here is deterministic and derived only from the silhouette.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import cv2
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from skimage.morphology import skeletonize

from app.geometry.depth import rasterize


class Region(IntEnum):
    BACKGROUND = 0
    TORSO = 1
    CHEST = 2
    BELLY = 3
    HEAD = 4
    NECK = 5
    LIMB = 6
    TAIL = 7
    EXTREMITY = 8

    @property
    def label(self) -> str:
        return self.name.lower()

    @property
    def group(self) -> str:
        """Coarse group used for piece naming."""
        return {
            Region.TORSO: "BODY",
            Region.CHEST: "BODY",
            Region.BELLY: "BODY",
            Region.HEAD: "HEAD",
            Region.NECK: "NECK",
            Region.LIMB: "LEG",
            Region.TAIL: "TAIL",
            Region.EXTREMITY: "EXTRA",
        }.get(self, "BODY")


TORSO_LIKE = (Region.TORSO, Region.CHEST, Region.BELLY)


@dataclass
class Appendage:
    kind: Region
    mask: np.ndarray  # bool raster
    attach: tuple[float, float]  # mm, where it joins the torso
    tip: tuple[float, float]  # mm, farthest point from the torso
    thickness_mm: float


@dataclass
class Anatomy:
    x0: float
    y0: float
    res: float  # mm per pixel; pixel (row, col) centre = (x0 + col*res, y0 + row*res)
    mask: np.ndarray  # bool
    dist: np.ndarray  # distance to the outline (mm)
    labels: np.ndarray  # Region codes
    along: np.ndarray  # 0 at the attachment of an appendage -> 1 at its tip (0 in the torso)
    appendages: list[Appendage]
    head_side: int  # +1 head at +x, -1 head at -x, 0 unknown
    skeleton: np.ndarray  # bool, pruned medial axis
    axis: np.ndarray  # (H, W, 2) unit axis direction on skeleton pixels (nan elsewhere)
    boundary: np.ndarray  # (N, 2) resampled outline points (mm)
    curvature: np.ndarray  # (N,) |curvature| normalised to ~[0, 1+]
    _tree: cKDTree = field(repr=False, default=None)

    # ------------------------------------------------------------------ sampling

    def to_px(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        c = np.clip(np.round((np.asarray(x) - self.x0) / self.res).astype(int), 0, self.mask.shape[1] - 1)
        r = np.clip(np.round((np.asarray(y) - self.y0) / self.res).astype(int), 0, self.mask.shape[0] - 1)
        return r, c

    def region_at(self, x: float, y: float) -> Region:
        r, c = self.to_px(x, y)
        return Region(int(self.labels[r, c]))

    def region_of_points(self, pts: np.ndarray) -> Region:
        """Majority region along a set of points (background ignored)."""
        r, c = self.to_px(pts[:, 0], pts[:, 1])
        vals = self.labels[r, c]
        vals = vals[vals > 0]
        if vals.size == 0:
            return Region.TORSO
        counts = np.bincount(vals, minlength=len(Region))
        # Group torso sub-labels so a rib through chest+belly counts as body.
        best = int(np.argmax(counts))
        return Region(best)

    def region_fraction(self, pts: np.ndarray, kinds: tuple[Region, ...]) -> float:
        r, c = self.to_px(pts[:, 0], pts[:, 1])
        vals = self.labels[r, c]
        vals = vals[vals > 0]
        return float(np.isin(vals, kinds).mean()) if vals.size else 0.0

    def curvature_near(self, pts: np.ndarray, radius: float) -> float:
        if self._tree is None:
            self._tree = cKDTree(self.boundary)
        best = 0.0
        for p in pts:
            idx = self._tree.query_ball_point(p, radius)
            if idx:
                best = max(best, float(np.max(self.curvature[idx])))
        return best

    def region_mask(self, kinds: tuple[Region, ...]) -> np.ndarray:
        return np.isin(self.labels, kinds)

    def region_area(self, kinds: tuple[Region, ...]) -> float:
        return float(self.region_mask(kinds).sum()) * self.res * self.res

    def summary(self) -> dict[str, float]:
        total = float(self.mask.sum()) or 1.0
        return {
            r.label: round(float((self.labels == r).sum()) / total, 4)
            for r in Region
            if r != Region.BACKGROUND and (self.labels == r).any()
        }


# ---------------------------------------------------------------------- helpers


def _disk(radius_px: float) -> np.ndarray:
    k = max(3, int(round(2 * radius_px)) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def _components(mask: np.ndarray, min_px: int = 1) -> list[np.ndarray]:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [labels == i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_px]


def _prune_skeleton(skel: np.ndarray, dist: np.ndarray, rounds: int = 3) -> np.ndarray:
    """Remove spurs: end branches shorter than ~1.3x the local radius at their junction."""
    skel = skel.copy()
    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    h, w = skel.shape

    def neighbours(r, c):
        return [(r + dr, c + dc) for dr, dc in offsets if 0 <= r + dr < h and 0 <= c + dc < w and skel[r + dr, c + dc]]

    for _ in range(rounds):
        changed = False
        kernel = np.ones((3, 3), np.uint8)
        counts = cv2.filter2D(skel.astype(np.uint8), -1, kernel, borderType=cv2.BORDER_CONSTANT) - skel
        ends = np.argwhere(skel & (counts == 1))
        for r0, c0 in ends:
            path = [(int(r0), int(c0))]
            prev = None
            cur = path[0]
            junction = None
            while True:
                nb = [p for p in neighbours(*cur) if p != prev and p not in path[-3:]]
                if len(nb) != 1:
                    junction = cur if len(nb) > 1 else None
                    break
                prev, cur = cur, nb[0]
                if len(neighbours(*cur)) > 2:
                    junction = cur
                    break
                path.append(cur)
                if len(path) > 4 * max(h, w):
                    break
            if junction is None:
                continue  # isolated segment: keep (it is the whole skeleton)
            if len(path) < 1.3 * dist[junction] + 2:
                for r, c in path:
                    skel[r, c] = False
                changed = True
        if not changed:
            break
    return skel


def _skeleton_axes(skel: np.ndarray, dist_px: np.ndarray) -> np.ndarray:
    axis = np.full(skel.shape + (2,), np.nan, dtype=np.float64)
    pts = np.argwhere(skel)
    if len(pts) < 2:
        return axis
    tree = cKDTree(pts)
    for (r, c) in pts:
        rad = max(4.0, 0.7 * float(dist_px[r, c]))
        idx = tree.query_ball_point((r, c), rad)
        if len(idx) < 3:
            continue
        nb = pts[idx].astype(np.float64)
        nb -= nb.mean(axis=0)
        cov = nb.T @ nb
        vals, vecs = np.linalg.eigh(cov)
        v = vecs[:, int(np.argmax(vals))]  # (row, col) = (y, x)
        axis[r, c] = (v[1], v[0])  # store as (x, y)
    return axis


def _boundary_curvature(poly: Polygon, step: float) -> tuple[np.ndarray, np.ndarray]:
    ring = poly.exterior
    n = max(32, int(ring.length / step))
    pts = np.array([ring.interpolate(i / n, normalized=True).coords[0] for i in range(n)])
    # Smooth the outline slightly so pixel noise is not mistaken for detail.
    k = 3
    padded = np.vstack([pts[-k:], pts, pts[:k]])
    kernel = np.ones(2 * k + 1) / (2 * k + 1)
    sm = np.stack([np.convolve(padded[:, i], kernel, mode="valid") for i in range(2)], axis=1)
    d1 = (np.roll(sm, -1, 0) - np.roll(sm, 1, 0)) / 2.0
    d2 = np.roll(sm, -1, 0) - 2 * sm + np.roll(sm, 1, 0)
    num = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    den = np.power(np.maximum((d1**2).sum(axis=1), 1e-12), 1.5)
    kappa = num / den  # 1/mm
    scale = np.percentile(kappa, 95) or 1.0
    return pts, np.clip(kappa / scale, 0.0, 3.0)


# ---------------------------------------------------------------------- analysis


def analyze(silhouette: Polygon, max_raster_side: int = 640) -> Anatomy:
    minx, miny, maxx, maxy = silhouette.bounds
    size = max(maxx - minx, maxy - miny)
    res = max(size / max_raster_side, 0.2)
    raster, x0, y0 = rasterize(silhouette, res, pad=3)
    mask = raster > 0
    dist_px = cv2.distanceTransform(raster, cv2.DIST_L2, 5).astype(np.float32)
    dmax = float(dist_px.max())
    total_px = int(mask.sum())

    # ---- torso: largest blob surviving an opening proportional to the thickest part
    core_r = max(2.0, 0.55 * dmax)
    opened = cv2.morphologyEx(raster, cv2.MORPH_OPEN, _disk(core_r)) > 0
    peak = np.unravel_index(int(np.argmax(dist_px)), dist_px.shape)
    torso = np.zeros_like(mask)
    for comp in _components(opened):
        if comp[peak]:
            torso = comp
    if not torso.any():
        torso = mask.copy()

    labels = np.zeros(mask.shape, dtype=np.uint8)
    labels[mask] = Region.TORSO
    along = np.zeros(mask.shape, dtype=np.float32)

    # Distance of every pixel from the torso (to measure how far appendages protrude).
    to_torso = cv2.distanceTransform((~torso).astype(np.uint8), cv2.DIST_L2, 5).astype(np.float32)
    ty, tx = np.nonzero(torso)
    t_cx, t_cy = float(tx.mean()), float(ty.mean())
    t_h = float(ty.max() - ty.min() + 1)
    t_w = float(tx.max() - tx.min() + 1)

    rest = mask & ~torso
    min_px = max(4, int(0.002 * total_px))
    raw = []
    for comp in _components(rest, min_px):
        protrusion = float(to_torso[comp].max())
        thick_px = 2.0 * float(dist_px[comp].max())
        if protrusion < 0.25 * core_r or (protrusion < 0.45 * core_r and protrusion < 1.5 * thick_px):
            continue  # fringe left by the opening along the torso outline: part of the torso
        near = comp & (to_torso <= 2.5)
        if not near.any():
            near = comp & (to_torso <= to_torso[comp].min() + 1.5)
        ay, ax = np.nonzero(near)
        attach = (float(ax.mean()), float(ay.mean()))
        tip_idx = np.argmax(np.where(comp, to_torso, -1))
        tip = (float(tip_idx % mask.shape[1]), float(tip_idx // mask.shape[1]))
        thick = 2.0 * float(dist_px[comp].max())
        raw.append({"mask": comp, "attach": attach, "tip": tip, "thick": thick, "protrusion": protrusion,
                    "area": int(comp.sum())})

    rows = np.nonzero(mask.any(axis=1))[0]
    ground, mask_h = float(rows.min()), float(rows.max() - rows.min() + 1)

    # ---- classify appendages
    appendages: list[Appendage] = []
    limb_ids, others = [], []
    for i, a in enumerate(raw):
        vx, vy = a["tip"][0] - a["attach"][0], a["tip"][1] - a["attach"][1]
        norm = np.hypot(vx, vy) or 1.0
        pointing_down = vy / norm < -0.6  # rows grow with y (y-up raster)
        low_attach = a["attach"][1] < t_cy - 0.15 * t_h
        elongated = a["protrusion"] >= 0.8 * a["thick"]
        reaches_ground = a["tip"][1] <= ground + 0.22 * mask_h  # legs stand on (or, mid-stride, just above) the lowest line
        if pointing_down and low_attach and elongated and reaches_ground:
            limb_ids.append(i)
        else:
            others.append(i)

    def elongation(a: dict) -> float:
        return a["protrusion"] / max(a["thick"], 1e-6)

    def end_side(a: dict) -> int:
        """+1/-1 when attached at the right/left end of the torso, 0 when on top/bottom."""
        off = (a["attach"][0] - t_cx) / (0.5 * t_w)
        return int(np.sign(off)) if abs(off) > 0.45 else 0

    # Animal-like: at least two legs. Otherwise every protrusion is a generic extremity.
    animal = len(limb_ids) >= 2
    head_idx = tail_idx = None
    head_side = 0
    if animal and others:
        def head_score(i):
            a = raw[i]
            elevated = a["tip"][1] >= t_cy - 0.25 * t_h
            side = abs(a["attach"][0] - t_cx) / (0.5 * t_w)
            return a["area"] * a["thick"] * (1.0 + 0.5 * min(side, 1.0)) * (1.0 if elevated else 0.15)

        cand = max(others, key=head_score)
        a = raw[cand]
        if a["tip"][1] >= t_cy - 0.25 * t_h and a["area"] >= 0.02 * total_px and a["thick"] >= 0.25 * dmax:
            head_idx = cand
            head_side = 1 if a["tip"][0] >= t_cx else -1
        else:
            # Head inside the torso blob (lion mane, elephant): the tail tells us the other end.
            tails = [i for i in others if end_side(raw[i]) != 0 and elongation(raw[i]) > 1.5]
            if tails:
                tail_idx = min(tails, key=lambda i: raw[i]["thick"])
                head_side = -end_side(raw[tail_idx])
                front = [i for i in others if end_side(raw[i]) == head_side and raw[i]["tip"][1] >= t_cy - 0.25 * t_h]
                if front:
                    head_idx = max(front, key=head_score)

    if head_idx is None and tail_idx is None and not any(
        head_side and end_side(raw[i]) == -head_side for i in others
    ):
        animal = False  # legs alone (e.g. two points of a star) do not make an animal
        head_side = 0

    for i, a in enumerate(raw):
        comp = a["mask"]
        prot = to_torso[comp]
        along[comp] = np.clip(prot / max(a["protrusion"], 1e-6), 0, 1)
        if not animal:
            kind = Region.EXTREMITY
        elif i in limb_ids:
            kind = Region.LIMB
        elif i == head_idx:
            kind = Region.NECK  # refined into neck/head/ears below
        elif i == tail_idx or (
            head_side and end_side(a) == -head_side and a["thick"] < 1.2 * dmax and elongation(a) > 1.5
        ):
            kind = Region.TAIL
        else:
            kind = Region.EXTREMITY
        labels[comp] = kind
        if kind == Region.NECK:
            _split_head(comp, labels, dist_px, to_torso, a)
        appendages.append(Appendage(
            kind=kind if kind != Region.NECK else Region.HEAD,
            mask=comp,
            attach=(x0 + a["attach"][0] * res, y0 + a["attach"][1] * res),
            tip=(x0 + a["tip"][0] * res, y0 + a["tip"][1] * res),
            thickness_mm=a["thick"] * res,
        ))

    # ---- torso sub-regions (chest on the head side, belly underneath)
    tmask = labels == Region.TORSO
    yy, xx = np.nonzero(tmask)
    if head_side and len(xx):
        chest = (xx - t_cx) * head_side > 0.22 * t_w
        labels[yy[chest], xx[chest]] = Region.CHEST
    belly = tmask & (np.arange(mask.shape[0])[:, None] < t_cy - 0.3 * t_h)
    if head_side:  # only meaningful for animals
        labels[belly & (labels == Region.TORSO)] = Region.BELLY

    # ---- skeleton and axes
    skel = skeletonize(mask)
    skel = _prune_skeleton(skel, dist_px)
    axis = _skeleton_axes(skel, dist_px)

    pts, kappa = _boundary_curvature(silhouette, step=max(0.5, size / 400.0))
    return Anatomy(
        x0=x0, y0=y0, res=res, mask=mask, dist=dist_px * res, labels=labels, along=along,
        appendages=appendages, head_side=head_side, skeleton=skel, axis=axis,
        boundary=pts, curvature=kappa,
    )


def _split_head(comp: np.ndarray, labels: np.ndarray, dist_px: np.ndarray, to_torso: np.ndarray, a: dict) -> None:
    """Split the head appendage into NECK (proximal), HEAD (distal blob) and EXTREMITY (ears,
    horns, antlers: thin protrusions of the head)."""
    local = dist_px * comp
    r = max(2.0, 0.5 * float(local.max()))
    blob = cv2.morphologyEx(comp.astype(np.uint8), cv2.MORPH_OPEN, _disk(r)) > 0
    # Reach of the thick part only, so antlers or ears do not define "distal".
    thick = cv2.morphologyEx(comp.astype(np.uint8), cv2.MORPH_OPEN, _disk(0.5 * r)) > 0
    reach = float(to_torso[thick].max()) if thick.any() else a["protrusion"]
    head = None
    for part in sorted(_components(blob, max(4, int(0.05 * comp.sum()))), key=lambda p: -float(to_torso[p].mean())):
        if float(to_torso[part].min()) > 0.2 * reach:  # a distal blob not touching the torso
            head = part
        break
    if head is None:
        head = comp & thick & (to_torso >= 0.6 * reach)
    if not head.any():
        head = comp & (to_torso >= 0.6 * a["protrusion"])
    head_start = float(to_torso[head].min())
    distal = comp & (to_torso >= head_start)
    labels[comp & ~distal] = Region.NECK
    labels[distal] = Region.HEAD
    grown = cv2.dilate(head.astype(np.uint8), _disk(2)) > 0
    away = cv2.distanceTransform((~grown).astype(np.uint8), cv2.DIST_L2, 3)
    for ext in _components(distal & ~grown, max(4, int(0.002 * comp.sum()))):
        if away[ext].max() >= 0.3 * r:
            labels[ext] = Region.EXTREMITY
