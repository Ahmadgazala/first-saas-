"""Foreground/background segmentation with multiple strategies and automatic selection.

Strategies (each produces a binary candidate mask):
  alpha       - transparent PNG/WEBP: alpha channel is authoritative
  background  - background colour model fitted to the image border (handles gradients)
  grabcut     - OpenCV GrabCut seeded from the background model
  threshold   - Otsu threshold on luminance (polarity chosen from the border)
  edges       - Canny edges, closed and hole-filled

Every candidate is cleaned (morphology, connected components, hole filling) and scored on
foreground/background colour separation, contour/edge alignment, size and border contact.
The best candidate wins; its score is reported as a confidence value so the UI can warn.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)

WORKING_MAX_SIDE = 768
METHODS = ("ai", "alpha", "background", "grabcut", "threshold", "edges")
LOW_CONFIDENCE = 0.6
# Prior trust in each strategy; colour-model based methods are preferred over raw luminance.
METHOD_PRIOR = {"ai": 1.15, "alpha": 1.0, "background": 1.0, "grabcut": 1.0, "threshold": 0.92, "edges": 0.88}


@dataclass
class Candidate:
    method: str
    mask: np.ndarray  # uint8 {0,255}
    score: float
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class SegmentationResult:
    mask: np.ndarray  # uint8 {0,255}, WHITE = object, BLACK = background
    method: str
    confidence: float
    warnings: list[str]
    candidates: list[dict]


# ----------------------------------------------------------------------------- helpers


def _kernel(size: int) -> np.ndarray:
    size = max(3, int(size) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def border_ring(shape: tuple[int, int], width: int) -> np.ndarray:
    ring = np.zeros(shape, dtype=bool)
    ring[:width, :] = True
    ring[-width:, :] = True
    ring[:, :width] = True
    ring[:, -width:] = True
    return ring


def fill_holes(mask: np.ndarray, max_hole_fraction: float = 1.0) -> np.ndarray:
    """Fill enclosed background regions smaller than max_hole_fraction of the object area."""
    binary = (mask > 0).astype(np.uint8)
    inv = 1 - binary
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    h, w = binary.shape
    area = max(1, int(binary.sum()))
    out = binary.copy()
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        touches_border = x == 0 or y == 0 or x + bw == w or y + bh == h
        if not touches_border and a <= max_hole_fraction * area:
            out[labels == i] = 1
    return out * 255


def select_main_component(mask: np.ndarray) -> np.ndarray:
    """Keep the most meaningful connected component: large and reasonably central."""
    binary = (mask > 0).astype(np.uint8)
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        return np.zeros_like(binary)
    h, w = binary.shape
    diag = np.hypot(w, h)
    best, best_score = 0, -1.0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        cx, cy = centroids[i]
        dist = np.hypot(cx - w / 2, cy - h / 2) / diag
        score = area * (1.0 - 0.8 * dist)
        if score > best_score:
            best, best_score = i, score
    return ((labels == best).astype(np.uint8)) * 255


def clean_mask(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    k = max(3, round(min(h, w) * 0.01))
    m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _kernel(k))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _kernel(k))
    m = select_main_component(m)
    m = fill_holes(m, max_hole_fraction=0.001)  # keep see-through openings (eye sockets, nostrils); styles that
    return m  # cannot use small ones fill them again (normalize.fill_small_holes)


def smooth_mask(mask: np.ndarray, sigma_fraction: float = 0.004) -> np.ndarray:
    h, w = mask.shape
    sigma = max(1.0, sigma_fraction * max(h, w))
    blurred = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), sigma)
    out = (blurred >= 127.5).astype(np.uint8) * 255
    return select_main_component(out)


# ----------------------------------------------------------------------------- strategies

_AI_SESSIONS: dict[str, object] = {}
_AI_LOCK = threading.Lock()


def _ai_session():
    """Shared rembg session for the configured model (None when unavailable)."""
    from app.core.config import get_settings

    name = get_settings().segmentation_model
    if name == "none":
        return None
    with _AI_LOCK:
        if name not in _AI_SESSIONS:
            try:
                import rembg

                _AI_SESSIONS[name] = rembg.new_session(name)
            except Exception:
                logger.warning("AI background removal unavailable", exc_info=True)
                _AI_SESSIONS[name] = None
        return _AI_SESSIONS[name]


def _ai_candidate(rgba: np.ndarray) -> np.ndarray | None:
    """Salient-object mask from a neural background-removal model (rembg / IS-Net)."""
    session = _ai_session()
    if session is None:
        return None
    try:
        from PIL import Image
        import rembg

        with _AI_LOCK:
            out = rembg.remove(Image.fromarray(rgba[..., :3]), session=session, only_mask=True)
        prob = np.asarray(out, dtype=np.uint8)
    except Exception:
        logger.warning("AI background removal failed", exc_info=True)
        return None
    return ((prob >= 128).astype(np.uint8)) * 255


def _alpha_candidate(rgba: np.ndarray) -> np.ndarray | None:
    alpha = rgba[..., 3]
    transparent = (alpha < 128).mean()
    if transparent < 0.02 or transparent > 0.98:
        return None
    return ((alpha >= 128).astype(np.uint8)) * 255


def _poly_features(h: int, w: int, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    u = xs / max(1, w - 1) - 0.5
    v = ys / max(1, h - 1) - 0.5
    return np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], axis=1)


def background_distance(lab: np.ndarray, ring_width: int) -> tuple[np.ndarray, float]:
    """Fit a smooth (quadratic) background colour model to the border pixels.

    Robust to the object touching the border: the fit is iteratively restricted to the
    60% of border pixels with the lowest residual. Returns the per-pixel colour distance
    to the predicted background and the border homogeneity (inlier ratio).
    """
    h, w = lab.shape[:2]
    ring = border_ring((h, w), ring_width)
    ys, xs = np.nonzero(ring)
    samples = lab[ys, xs].astype(np.float64)
    feats = _poly_features(h, w, ys.astype(np.float64), xs.astype(np.float64))
    keep = np.ones(len(samples), dtype=bool)
    coef = None
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(feats[keep], samples[keep], rcond=None)
        resid = np.linalg.norm(feats @ coef - samples, axis=1)
        keep = resid <= np.quantile(resid, 0.6)
    resid = np.linalg.norm(feats @ coef - samples, axis=1)
    homogeneity = float((resid < 12.0).mean())

    gy, gx = np.mgrid[0:h, 0:w]
    all_feats = _poly_features(h, w, gy.ravel().astype(np.float64), gx.ravel().astype(np.float64))
    predicted = (all_feats @ coef).reshape(h, w, 3)
    dist = np.linalg.norm(lab.astype(np.float64) - predicted, axis=2)
    return dist.astype(np.float32), homogeneity


def _background_candidate(dist: np.ndarray) -> np.ndarray:
    d8 = np.clip(dist * 2.0, 0, 255).astype(np.uint8)
    otsu, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(otsu, 24.0)  # at least ~12 Lab units from the background model
    return ((d8 > thr).astype(np.uint8)) * 255


def _grabcut_candidate(bgr: np.ndarray, dist: np.ndarray, ring_width: int) -> np.ndarray | None:
    h, w = dist.shape
    # GrabCut's colour GMMs degenerate (and OpenCV can spin for minutes) when the image has
    # (almost) no colour variation or nothing differs from the background: no candidate then.
    if float(dist.max()) < 12.0 or float(bgr.reshape(-1, 3).std(axis=0).max()) < 3.0:
        return None
    gc = np.full((h, w), cv2.GC_PR_BGD, dtype=np.uint8)
    hi = np.quantile(dist, 0.8)
    gc[dist >= max(hi, 20.0)] = cv2.GC_PR_FGD
    ring = border_ring((h, w), ring_width)
    gc[ring & (dist < 10.0)] = cv2.GC_BGD
    # Central prior: the subject is usually near the middle of a photo.
    cy0, cy1, cx0, cx1 = int(h * 0.35), int(h * 0.65), int(w * 0.35), int(w * 0.65)
    centre = gc[cy0:cy1, cx0:cx1]
    centre[centre == cv2.GC_PR_BGD] = cv2.GC_PR_FGD
    if not (gc == cv2.GC_BGD).any() or not ((gc == cv2.GC_PR_FGD) | (gc == cv2.GC_FGD)).any():
        return None
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(bgr, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        logger.warning("GrabCut failed", exc_info=True)
        return None
    fg = (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)
    return (fg.astype(np.uint8)) * 255


def _threshold_candidate(gray: np.ndarray, ring_width: int) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ring = border_ring(gray.shape, ring_width)
    if (th[ring] > 0).mean() > 0.5:  # border is mostly "on": object is the dark side
        th = 255 - th
    return th


def _edges_candidate(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    med = float(np.median(blur))
    edges = cv2.Canny(blur, int(max(0, 0.66 * med)), int(min(255, 1.33 * med)) or 50)
    k = max(3, round(min(h, w) * 0.012))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, _kernel(k))
    closed = cv2.dilate(closed, _kernel(3))
    return fill_holes(closed, max_hole_fraction=1e9)


# ----------------------------------------------------------------------------- scoring


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    fa, fb = a > 0, b > 0
    union = np.logical_or(fa, fb).sum()
    return float(np.logical_and(fa, fb).sum() / union) if union else 0.0


def _histogram_separation(lab: np.ndarray, fg: np.ndarray) -> float:
    bins = [8, 8, 8]
    ranges = [0, 256, 0, 256, 0, 256]
    m_fg = fg.astype(np.uint8)
    m_bg = (~fg).astype(np.uint8)
    h_fg = cv2.calcHist([lab], [0, 1, 2], m_fg, bins, ranges)
    h_bg = cv2.calcHist([lab], [0, 1, 2], m_bg, bins, ranges)
    cv2.normalize(h_fg, h_fg, 1.0, 0.0, cv2.NORM_L1)
    cv2.normalize(h_bg, h_bg, 1.0, 0.0, cv2.NORM_L1)
    return float(cv2.compareHist(h_fg, h_bg, cv2.HISTCMP_BHATTACHARYYA))


def score_candidate(
    mask: np.ndarray, lab: np.ndarray, grad: np.ndarray, bg_dist: np.ndarray, homogeneity: float
) -> tuple[float, dict[str, float]]:
    h, w = mask.shape
    fg = mask > 0
    area = float(fg.mean())
    if area < 0.01 or area > 0.97:
        return 0.0, {"area": area}

    separation = _histogram_separation(lab, fg)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = np.vstack([c.reshape(-1, 2) for c in contours])
    inside = (pts[:, 0] > 1) & (pts[:, 0] < w - 2) & (pts[:, 1] > 1) & (pts[:, 1] < h - 2)
    pts = pts[inside]
    if len(pts) < 10:
        return 0.0, {"area": area}
    edge_strength = float(grad[pts[:, 1], pts[:, 0]].mean() / (np.percentile(grad, 90) + 1e-6))
    edge_alignment = float(np.clip(edge_strength, 0.0, 1.5) / 1.5)

    sides = [fg[0, :].mean(), fg[-1, :].mean(), fg[:, 0].mean(), fg[:, -1].mean()]
    sides_touched = sum(1 for s in sides if s > 0.05)
    border_fraction = float(np.mean(sides))
    border_penalty = 1.0
    if sides_touched >= 3:
        border_penalty *= 0.55
    if border_fraction > 0.4:
        border_penalty *= 0.5

    hull = cv2.convexHull(np.vstack(contours))
    solidity = float(fg.sum() / max(1.0, cv2.contourArea(hull)))
    size_factor = 1.0 if 0.04 <= area <= 0.85 else 0.6

    score = (0.6 * separation + 0.4 * edge_alignment) * border_penalty * size_factor * (0.6 + 0.4 * min(1.0, solidity / 0.6))

    # When the border is well explained by the background model, pixels left outside the
    # mask should look like background. Catches partial objects (e.g. only a white belly).
    outside = ~fg
    bg_consistency = float((bg_dist[outside] < 18.0).mean()) if outside.any() else 0.0
    if homogeneity > 0.6:
        score *= 0.5 + 0.5 * bg_consistency
    metrics = {
        "area": round(area, 4),
        "separation": round(separation, 4),
        "edge_alignment": round(edge_alignment, 4),
        "border_fraction": round(border_fraction, 4),
        "sides_touched": float(sides_touched),
        "solidity": round(solidity, 4),
        "bg_consistency": round(bg_consistency, 4),
    }
    return float(score), metrics


# ----------------------------------------------------------------------------- entry point


def segment(rgba: np.ndarray, method: str = "auto") -> SegmentationResult:
    """Segment the main object of an RGBA image (any size). Returns a mask at working size."""
    from app.vision.image_io import resize_max_side

    if method != "auto" and method not in METHODS:
        raise ValueError(f"unknown segmentation method {method!r}")

    rgba = resize_max_side(rgba, WORKING_MAX_SIDE)
    h, w = rgba.shape[:2]
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    # Composite onto white so transparent pixels have a defined colour.
    rgb = (rgba[..., :3].astype(np.float32) * alpha + 255.0 * (1 - alpha)).astype(np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.magnitude(gx, gy)
    ring_width = max(2, round(min(h, w) * 0.02))

    warnings: list[str] = []
    raw: dict[str, np.ndarray] = {}

    alpha_mask = _alpha_candidate(rgba)
    if alpha_mask is not None:
        raw["alpha"] = alpha_mask

    use_all = method == "auto" and alpha_mask is None
    if use_all or method == "ai":
        ai = _ai_candidate(rgba)
        if ai is not None:
            raw["ai"] = ai
        elif method == "ai":
            warnings.append("AI background removal is not available; automatic detection was used instead.")
            method, use_all = "auto", alpha_mask is None
    dist, homogeneity = background_distance(lab, ring_width)
    if use_all or method == "background":
        raw["background"] = _background_candidate(dist)
    if use_all or method == "grabcut":
        gc = _grabcut_candidate(bgr, dist, ring_width)
        if gc is not None:
            raw["grabcut"] = gc
    if use_all or method == "threshold":
        raw["threshold"] = _threshold_candidate(gray, ring_width)
    if use_all or method == "edges":
        raw["edges"] = _edges_candidate(gray)

    if method == "alpha" and alpha_mask is None:
        fallback = segment(rgba, "auto")
        fallback.warnings.insert(0, "The image has no usable transparency; automatic detection was used instead.")
        return fallback

    candidates: list[Candidate] = []
    for name, m in raw.items():
        if method != "auto" and name != method:
            continue
        cleaned = clean_mask(m)
        s, metrics = score_candidate(cleaned, lab, grad, dist, homogeneity)
        s *= METHOD_PRIOR[name]
        if name == "alpha":
            s = max(s, 0.95)  # an explicit alpha channel is authoritative
        elif name == "background" and homogeneity > 0.85:
            s *= 1.15  # uniform or smoothly varying background: the colour model is reliable
        candidates.append(Candidate(name, cleaned, s, metrics))

    candidates.sort(key=lambda c: c.score, reverse=True)
    # The neural model knows what "the main object" is; the classical scoring heuristics
    # (border contact, colour separation) are tuned for classical masks and would veto a
    # correct mask of a subject that fills the frame. Prefer it unless it is degenerate or
    # clearly worse.
    ai = next((c for c in candidates if c.method == "ai"), None)
    if ai is not None and candidates[0] is not ai and method == "auto":
        area = ai.metrics.get("area", 0.0)
        if 0.02 < area < 0.97 and ai.score >= 0.6 * candidates[0].score:
            candidates.remove(ai)
            candidates.insert(0, ai)
    summary = [{"method": c.method, "score": round(c.score, 3), **c.metrics} for c in candidates]
    logger.debug("background homogeneity=%.3f", homogeneity)
    best = candidates[0] if candidates else None
    if best is None or best.score <= 0.0 or not (best.mask > 0).any():
        return SegmentationResult(np.zeros((h, w), np.uint8), "none", 0.0, ["Unable to detect the object."], summary)

    mask = smooth_mask(best.mask)
    # Independent methods agreeing on the same region is strong evidence of a correct result.
    others = [c for c in candidates[1:] if c.score > 0]
    agreement = max((mask_iou(best.mask, c.mask) for c in others), default=1.0 if best.method == "alpha" else 0.5)
    # Poorly explained surroundings (subject fills the frame, busy scene) lower confidence.
    consistency = best.metrics.get("bg_consistency", 1.0)
    confidence = best.score * (0.55 + 0.45 * agreement) * (0.6 + 0.4 * min(1.0, consistency / 0.7))
    confidence = float(np.clip(confidence, 0.0, 1.0))
    if best.method == "alpha":
        confidence = max(confidence, 0.95)
    fg = mask > 0
    sides = [fg[0, :].mean(), fg[-1, :].mean(), fg[:, 0].mean(), fg[:, -1].mean()]
    if sum(1 for s in sides if s > 0.05) >= 2:
        warnings.append("The object touches the image border; parts of it may be cropped.")
    if confidence < LOW_CONFIDENCE:
        warnings.append(
            "Segmentation confidence is low. Check the silhouette carefully, try another detection "
            "method, or use an image with a plain or transparent background."
        )
    logger.info("segmentation method=%s confidence=%.3f candidates=%s", best.method, confidence, summary)
    return SegmentationResult(mask, best.method, confidence, warnings, summary)
