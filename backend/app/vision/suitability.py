"""ImageSuitabilityAnalyzer: is this photo a good source for a structural kit?

Runs before any expensive geometry. Every score is an *estimated* confidence in [0, 1]
computed by a documented heuristic (no trained model is involved):

  resolution_score       shorter image side: 0 at 64 px, 1 from 512 px
  silhouette_confidence  the segmentation's own score (agreement of candidate masks,
                         edge alignment, compactness - see vision/segmentation.py)
  separation_score       colour distance (CIE Lab) between the object and the background
                         ring, 1 at >= 40 Lab units
  single_subject_score   1 when no second foreground blob of comparable size exists
                         (blobs found by background-colour distance), lower otherwise
  visibility_score       share of the object outline that is NOT on the image border
                         (a subject cut off by the frame is partly hidden)
  size_score             object area share of the image, ideal 4 % - 80 %
  structure_confidence   StructureAnalyzer confidence (axis smoothness, head and limb
                         clarity) on the extracted silhouette
  pose_score             elongation of the silhouette: side views of animals are wider
                         than tall or clearly elongated; a near-round blob suggests a
                         frontal view

overall_confidence = weighted mean; `suitable` additionally requires that no blocking
problem was found (no subject, several comparable subjects, subject far too small).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

WEIGHTS = {
    "silhouette_confidence": 0.25,
    "structure_confidence": 0.2,
    "separation_score": 0.12,
    "single_subject_score": 0.13,
    "visibility_score": 0.1,
    "size_score": 0.08,
    "resolution_score": 0.06,
    "pose_score": 0.06,
}
MIN_OVERALL = 0.5
GUIDANCE = "Please upload a clearer side-view image containing one main subject on a plain background."


@dataclass
class Suitability:
    suitable: bool
    overall_confidence: float
    silhouette_confidence: float
    structure_confidence: float
    recommended_view: str
    scores: dict[str, float]
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    guidance: str | None = None
    structure: dict | None = None
    skeleton: dict | None = None  # detected, editable skeleton in image fractions (x right, y down)
    confidence_kind: str = "estimated"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["overall_confidence"] = round(d["overall_confidence"], 3)
        d["silhouette_confidence"] = round(d["silhouette_confidence"], 3)
        d["structure_confidence"] = round(d["structure_confidence"], 3)
        d["scores"] = {k: round(v, 3) for k, v in d["scores"].items()}
        return d


class ImageSuitabilityAnalyzer:
    def __init__(self, structure_size_mm: float = 250.0):
        self.size_mm = structure_size_mm

    def analyze(self, rgba: np.ndarray, mask: np.ndarray, silhouette_confidence: float) -> Suitability:
        h, w = rgba.shape[:2]
        if mask.shape[:2] != (h, w):
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        fg = mask > 127
        warnings: list[str] = []
        blocking: list[str] = []
        scores: dict[str, float] = {}

        side = min(h, w)
        scores["resolution_score"] = float(np.clip((side - 64) / (512 - 64), 0, 1))
        if side < 300:
            warnings.append(f"Low resolution ({w}×{h} px): fine details will be lost.")

        area = float(fg.mean())
        if area < 0.002:
            blocking.append("No clear subject was found in the image.")
        scores["size_score"] = float(np.clip(min(area / 0.04, 1.0) * min((0.97 - area) / 0.17, 1.0), 0, 1))
        if 0.002 <= area < 0.02:
            warnings.append("The subject is very small in the image; crop closer for more detail.")
        if area > 0.85:
            warnings.append("The subject fills almost the whole image; leave some background around it.")

        scores["silhouette_confidence"] = float(np.clip(silhouette_confidence, 0, 1))
        if silhouette_confidence < 0.5:
            warnings.append("The object outline was hard to separate from the background.")

        lab = cv2.cvtColor(_rgb(rgba), cv2.COLOR_RGB2LAB).astype(np.float32)
        ring = _ring(fg, max(3, side // 50))
        if fg.any() and ring.any():
            dist = float(np.linalg.norm(np.median(lab[fg], axis=0) - np.median(lab[ring], axis=0)))
            scores["separation_score"] = float(np.clip(dist / 40.0, 0, 1))
            bg_std = float(np.mean(np.std(lab[ring], axis=0)))
            if bg_std > 30:
                warnings.append("The background is busy; a plain background gives a cleaner outline.")
                scores["separation_score"] *= 0.8
        else:
            scores["separation_score"] = 0.0
        if scores["separation_score"] < 0.3:
            warnings.append("Low contrast between the subject and the background.")

        # Other foreground blobs: pixels far from the background colour, outside the subject.
        # Background colours: k-means clusters of the image border (sky + ground, gradients).
        border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]).astype(np.float32)
        k = int(min(4, max(1, len(border) // 50)))
        _, _, centers = cv2.kmeans(border, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                                   3, cv2.KMEANS_PP_CENTERS)
        dist_bg = np.min(np.stack([np.linalg.norm(lab - c, axis=2) for c in centers]), axis=0)
        far = dist_bg > 28
        grown = cv2.dilate(fg.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        others = (far & ~grown).astype(np.uint8)
        others = cv2.morphologyEx(others, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(others, connectivity=8)
        subject_px = max(int(fg.sum()), 1)
        big = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.25 * subject_px]
        if big:
            scores["single_subject_score"] = float(np.clip(1.0 - 0.45 * len(big), 0, 1))
            blocking.append("The image seems to contain more than one main subject.")
        else:
            medium = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.06 * subject_px]
            scores["single_subject_score"] = 1.0 if not medium else 0.75
            if medium:
                warnings.append("Other objects are visible; they are ignored.")

        contour_px = _outline(fg)
        if contour_px.size:
            edge = ((contour_px[:, 0] <= 1) | (contour_px[:, 0] >= h - 2) | (contour_px[:, 1] <= 1)
                    | (contour_px[:, 1] >= w - 2)).mean()
            scores["visibility_score"] = float(np.clip(1.0 - 3.0 * edge, 0, 1))
            if edge > 0.2:
                blocking.append("The subject is cut off by the image border; the whole body should be visible.")
            elif edge > 0.05:
                warnings.append("The subject touches the image border; parts of it may be cut off.")
        else:
            scores["visibility_score"] = 0.0

        ys, xs = np.nonzero(fg)
        structure = skeleton = None
        scores["structure_confidence"] = 0.0
        scores["pose_score"] = 0.0
        recommended = "side"
        if len(xs) > 50:
            pts = np.stack([xs, ys], 1).astype(np.float64)
            cov = np.cov((pts - pts.mean(0)).T)
            ev = np.sort(np.linalg.eigvalsh(cov))
            elong = float(np.sqrt(ev[1] / max(ev[0], 1e-9)))
            scores["pose_score"] = float(np.clip((elong - 1.05) / 0.6, 0, 1))
            if elong < 1.2:
                warnings.append("The outline is nearly round - a side view usually shows the structure best.")
            structure, sconf, skeleton = self._structure(mask)
            scores["structure_confidence"] = sconf
            if structure is not None and not structure.get("has_head") and structure.get("limb_count", 0) == 0:
                warnings.append("No head or limbs were recognised: the kit will follow the main axis only.")

        overall = sum(WEIGHTS[k] * scores.get(k, 0.0) for k in WEIGHTS) / sum(WEIGHTS.values())
        suitable = overall >= MIN_OVERALL and not blocking
        return Suitability(
            suitable=suitable,
            overall_confidence=float(overall),
            silhouette_confidence=scores["silhouette_confidence"],
            structure_confidence=scores["structure_confidence"],
            recommended_view=recommended,
            scores=scores,
            warnings=warnings,
            blocking=blocking,
            guidance=None if suitable else GUIDANCE,
            structure=structure,
            skeleton=skeleton,
        )

    def _structure(self, mask: np.ndarray) -> tuple[dict | None, float, dict | None]:
        from app.core.errors import PuzzleError
        from app.geometry.kit.structure import ImageFrame, StructureAnalyzer
        from app.geometry.normalize import normalize_silhouette

        try:
            sil = normalize_silhouette(mask, self.size_mm, 2.5)
            st = StructureAnalyzer().analyze(sil.polygon)
            skeleton = ImageFrame(mask, sil.polygon).skeleton_to_fractions(st.to_skeleton())
        except (PuzzleError, ValueError, IndexError):
            return None, 0.0, None
        return st.to_dict(), float(st.confidence["structure"]), skeleton


def _rgb(rgba: np.ndarray) -> np.ndarray:
    if rgba.shape[2] == 3:
        return rgba
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return (rgba[..., :3].astype(np.float32) * a + 255.0 * (1 - a)).astype(np.uint8)


def _ring(fg: np.ndarray, width: int) -> np.ndarray:
    k = np.ones((2 * width + 1, 2 * width + 1), np.uint8)
    outer = cv2.dilate(fg.astype(np.uint8), k) > 0
    return outer & ~fg


def _outline(fg: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(fg.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return np.zeros((0, 2), int)
    c = max(contours, key=cv2.contourArea)[:, 0, :]
    return np.stack([c[:, 1], c[:, 0]], 1)  # (row, col)


def structure_overlay(original_png: bytes, mask_png: bytes, size_mm: float = 250.0) -> bytes | None:
    """Preview of the detected structure: silhouette tint, spine curve with head / neck /
    torso / tail colouring and limb axes, drawn over the photo."""
    import io

    from PIL import Image

    from app.geometry.kit.structure import StructureAnalyzer
    from app.geometry.normalize import normalize_silhouette

    img = np.asarray(Image.open(io.BytesIO(original_png)).convert("RGB")).copy()
    mask = np.asarray(Image.open(io.BytesIO(mask_png)).convert("L"))
    h, w = img.shape[:2]
    if mask.shape != (h, w):
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    try:
        sil = normalize_silhouette(mask, size_mm, 2.5)
        st = StructureAnalyzer().analyze(sil.polygon)
    except Exception:  # noqa: BLE001 - preview only
        return None
    ys, xs = np.nonzero(mask > 127)
    if not len(xs):
        return None
    # Normalised mm -> image px: the silhouette was centred and scaled to size_mm.
    minx, miny, maxx, maxy = sil.polygon.bounds
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    s = max(x1 - x0, y1 - y0) / max(maxx - minx, maxy - miny)

    def px(p):
        return int(round(x0 + (p[0] - minx) * s)), int(round(y1 - (p[1] - miny) * s))

    tint = img.copy()
    tint[mask > 127] = (0.55 * tint[mask > 127] + 0.45 * np.array([37, 99, 235])).astype(np.uint8)
    img = tint
    colors = {"head": (239, 68, 68), "neck": (245, 158, 11), "torso": (16, 185, 129), "tail": (139, 92, 246)}
    sp = st.spine
    lw = max(2, int(round(min(h, w) / 160)))
    for name, (a, b) in st.ranges.items():
        if name == "spine":
            continue
        m = (sp.s >= a) & (sp.s <= b)
        pts = np.array([px(p) for p in sp.pts[m]], np.int32)
        if len(pts) >= 2:
            cv2.polylines(img, [pts], False, colors.get(name, (255, 255, 255)), lw * 2, cv2.LINE_AA)
    for lb in st.limbs:
        pts = np.array([px(p) for p in lb.axis], np.int32)
        cv2.polylines(img, [pts], False, (14, 165, 233), lw * 2, cv2.LINE_AA)
        cv2.circle(img, px(lb.attach), lw * 3, (255, 255, 255), -1, cv2.LINE_AA)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()
