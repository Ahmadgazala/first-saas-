"""Upload validation: size limits, magic-byte sniffing, filename sanitization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.core.errors import ErrorCode, PuzzleError

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


@dataclass(frozen=True)
class SniffedType:
    format: str  # PNG | JPEG | WEBP
    extension: str
    content_type: str


def sniff_image_type(data: bytes) -> SniffedType:
    """Identify the image type from magic bytes; the client-supplied type is never trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return SniffedType("PNG", "png", "image/png")
    if data.startswith(b"\xff\xd8\xff"):
        return SniffedType("JPEG", "jpg", "image/jpeg")
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return SniffedType("WEBP", "webp", "image/webp")
    raise PuzzleError(
        ErrorCode.UNSUPPORTED_FORMAT,
        "Unsupported file type. Please upload a PNG, JPG or WEBP image.",
        f"magic={data[:12]!r}",
    )


MODEL_EXTENSIONS = {"stl", "obj", "glb", "ply"}


def sniff_model_type(data: bytes, filename: str | None = None) -> str | None:
    """Identify a 3D model from its content (STL binary/ASCII, OBJ, GLB, PLY) or None."""
    if data[:4] == b"glTF":
        return "glb"
    if data[:3] == b"ply":
        return "ply"
    head = data[:65536]
    if head[:5].lower() == b"solid" and b"facet" in head:
        return "stl"
    if len(data) >= 84:
        count = int.from_bytes(data[80:84], "little")
        if count > 0 and 84 + 50 * count == len(data):
            return "stl"
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = [ln.strip() for ln in text.splitlines()]
    if any(ln.startswith("v ") for ln in lines) and (
        any(ln.startswith("f ") for ln in lines) or (filename or "").lower().endswith(".obj")
    ):
        return "obj"
    return None


def validate_upload_size(size: int, max_bytes: int) -> None:
    if size <= 0:
        raise PuzzleError(ErrorCode.INVALID_FILE, "The uploaded file is empty.")
    if size > max_bytes:
        raise PuzzleError(
            ErrorCode.FILE_TOO_LARGE,
            f"The file is too large. Maximum upload size is {max_bytes // (1024 * 1024)} MB.",
            f"size={size}",
        )


def sanitize_filename(name: str | None, max_length: int = 80) -> str:
    """Return a display-safe filename. It is only stored as metadata, never used as a path."""
    if not name:
        return "image"
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")
    name = re.sub(r"_+", "_", name)
    if not name:
        return "image"
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 5:
            name = stem[: max_length - len(ext) - 1] + "." + ext
        else:
            name = name[:max_length]
    return name
