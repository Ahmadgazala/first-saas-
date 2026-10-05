"""Deterministic synthetic silhouettes and images for geometry tests."""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

SIZE = 512


def _canvas() -> np.ndarray:
    return np.zeros((SIZE, SIZE), dtype=np.uint8)


def circle_mask() -> np.ndarray:
    m = _canvas()
    cv2.circle(m, (256, 256), 180, 255, -1)
    return m


def rectangle_mask() -> np.ndarray:
    m = _canvas()
    cv2.rectangle(m, (60, 160), (452, 352), 255, -1)
    return m


def animal_mask() -> np.ndarray:
    """Side view of a four-legged animal: body, neck, head, ears, four legs and a tail."""
    m = _canvas()
    cv2.ellipse(m, (240, 250), (150, 75), 0, 0, 360, 255, -1)  # body
    neck = np.array([[340, 220], [395, 120], [430, 140], [385, 260]], np.int32)
    cv2.fillPoly(m, [neck], 255)
    cv2.ellipse(m, (430, 125), (55, 32), -20, 0, 360, 255, -1)  # head
    cv2.fillPoly(m, [np.array([[395, 100], [405, 60], [420, 98]], np.int32)], 255)  # ear
    for x in (130, 175, 300, 345):  # legs
        cv2.rectangle(m, (x - 14, 280), (x + 14, 455), 255, -1)
    cv2.fillPoly(m, [np.array([[95, 225], [40, 300], [55, 308], [110, 250]], np.int32)], 255)  # tail
    return m


def irregular_mask() -> np.ndarray:
    m = _canvas()
    rng = np.random.default_rng(7)
    angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    radii = 150 + rng.uniform(-45, 45, len(angles))
    pts = np.stack([256 + radii * np.cos(angles), 256 + radii * np.sin(angles)], axis=1).astype(np.int32)
    cv2.fillPoly(m, [pts], 255)
    return cv2.GaussianBlur(m, (9, 9), 0) > 127


def thin_parts_mask() -> np.ndarray:
    """A body with very thin antennae/whiskers that must be removed for manufacturing."""
    m = _canvas()
    cv2.ellipse(m, (256, 300), (170, 90), 0, 0, 360, 255, -1)
    cv2.line(m, (200, 220), (120, 40), 255, 2)  # 2 px wide: far below the min feature
    cv2.line(m, (310, 220), (400, 40), 255, 2)
    cv2.rectangle(m, (420, 290), (500, 296), 255, -1)
    return m


def concave_mask() -> np.ndarray:
    """A deep C / horseshoe shape."""
    m = _canvas()
    cv2.ellipse(m, (256, 256), (200, 170), 0, 0, 360, 255, -1)
    cv2.ellipse(m, (300, 256), (130, 90), 0, 0, 360, 0, -1)
    cv2.rectangle(m, (300, 200), (511, 312), 0, -1)
    return m


ALL_MASKS = {
    "circle": circle_mask,
    "rectangle": rectangle_mask,
    "animal": animal_mask,
    "irregular": lambda: (irregular_mask().astype(np.uint8) * 255),
    "thin_parts": thin_parts_mask,
    "concave": concave_mask,
}


def mask_to_photo(mask: np.ndarray, *, background=(235, 238, 242), foreground=(120, 72, 40), noise: float = 6.0) -> np.ndarray:
    """Render a mask as an RGB 'photo' with shading and sensor noise."""
    rng = np.random.default_rng(3)
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    bg = np.array(background, np.float32)[None, None, :] * (0.92 + 0.08 * (yy / h))[..., None]
    fg = np.array(foreground, np.float32)[None, None, :] * (0.8 + 0.4 * (xx / w))[..., None]
    img = np.where((mask > 0)[..., None], fg, bg) + rng.normal(0, noise, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def encode(array: np.ndarray, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format=fmt)
    return buf.getvalue()


def transparent_png(mask: np.ndarray) -> bytes:
    rgb = mask_to_photo(mask)
    rgba = np.dstack([rgb, np.where(mask > 0, 255, 0).astype(np.uint8)])
    return encode(rgba, "PNG")
