"""Safe image decoding.

Images are decoded with Pillow under a decompression-bomb guard, EXIF orientation is
applied, and the result is always an RGBA uint8 numpy array. Uploaded bytes are never
written anywhere executable and never interpreted beyond image decoding.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import ErrorCode, PuzzleError
from app.validators.file_validators import sniff_image_type

# Hard cap on decoded pixels independent of the per-side limit (protects against bombs).
_MAX_PIXELS = 8192 * 8192


@dataclass
class LoadedImage:
    rgba: np.ndarray  # H x W x 4, uint8
    format: str
    width: int
    height: int
    has_alpha: bool
    warnings: list[str] = field(default_factory=list)


def load_image(
    data: bytes,
    *,
    max_side: int = 8192,
    min_side: int = 64,
    recommended_side: int = 4096,
) -> LoadedImage:
    sniffed = sniff_image_type(data)
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format not in ("PNG", "JPEG", "WEBP") or probe.format != sniffed.format:
                    raise PuzzleError(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        "Unsupported or mismatched image format.",
                        f"pillow={probe.format} sniffed={sniffed.format}",
                    )
                width, height = probe.size
                _check_resolution(width, height, max_side, min_side)
                probe.verify()
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img = ImageOps.exif_transpose(img)
                has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
                rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8).copy()
    except PuzzleError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PuzzleError(ErrorCode.INVALID_RESOLUTION, "The image resolution is too large.", repr(exc)) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise PuzzleError(ErrorCode.INVALID_FILE, "The image file is corrupted or cannot be read.", repr(exc)) from exc

    h, w = rgba.shape[:2]
    notes: list[str] = []
    if max(w, h) > recommended_side:
        notes.append(
            f"Image is larger than the recommended {recommended_side}px; it was downscaled for processing."
        )
    return LoadedImage(rgba=rgba, format=sniffed.format, width=w, height=h, has_alpha=has_alpha, warnings=notes)


def _check_resolution(width: int, height: int, max_side: int, min_side: int) -> None:
    if width < min_side or height < min_side:
        raise PuzzleError(
            ErrorCode.INVALID_RESOLUTION,
            f"The image is too small. Minimum resolution is {min_side}×{min_side} px.",
            f"{width}x{height}",
        )
    if width > max_side or height > max_side:
        raise PuzzleError(
            ErrorCode.INVALID_RESOLUTION,
            f"The image is too large. Maximum resolution is {max_side}×{max_side} px.",
            f"{width}x{height}",
        )


def to_png_bytes(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def resize_max_side(rgba: np.ndarray, max_side: int) -> np.ndarray:
    import cv2

    h, w = rgba.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1.0:
        return rgba
    return cv2.resize(rgba, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
