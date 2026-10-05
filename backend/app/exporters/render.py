"""Software renderer for the generated structure (preview.png and assembly-guide figures).

Each piece is extruded from its exported polygon by the material thickness and drawn with
an orthographic camera, a per-pixel depth buffer and flat two-sided lighting. Outlines are
derived from discontinuities in the face-ID buffer. Planar faces are filled with OpenCV and
depth is evaluated from each face's plane equation, so no triangulation is needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.geometry.types import Piece, PieceKind, PuzzleModel

PROFILE_COLOR = (214, 170, 118)
CROSS_COLOR = (196, 146, 92)
HIGHLIGHT_COLOR = (37, 99, 235)
GHOST_COLOR = (226, 221, 214)
# Fossil kits are shown in white material (like a white plywood / acrylic skeleton kit).
KIT_PLATE_COLOR = (240, 238, 232)
KIT_CROSS_COLOR = (226, 223, 215)
BACKGROUND = (255, 255, 255)
LIGHT = np.array([0.35, 0.8, 0.5]) / np.linalg.norm([0.35, 0.8, 0.5])


@dataclass
class RenderItem:
    piece: Piece
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    color: tuple[int, int, int] | None = None


@dataclass
class Camera:
    azimuth_deg: float = 38.0
    elevation_deg: float = 24.0

    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        az, el = math.radians(self.azimuth_deg), math.radians(self.elevation_deg)
        d = np.array([math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)])
        f = -d
        r = np.cross(f, [0.0, 1.0, 0.0])
        r /= np.linalg.norm(r)
        u = np.cross(r, f)
        return r, u, f


@dataclass
class Face:
    rings: list[np.ndarray]  # 3D rings (N x 3)
    normal: np.ndarray
    item: int
    color: tuple[int, int, int]


@dataclass
class RenderResult:
    image: Image.Image
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)  # label positions (px)


def _faces(item: RenderItem, index: int, thickness: float, simplify: float) -> list[Face]:
    pc = item.piece
    t = pc.transform
    poly = pc.polygon.simplify(simplify, preserve_topology=True) if simplify else pc.polygon
    origin = np.array(t.origin) + np.array(item.offset)
    U, V, N = np.array(t.u_axis), np.array(t.v_axis), np.array(t.normal)
    color = item.color or _default_color(pc)

    def lift(coords: np.ndarray, w: float) -> np.ndarray:
        return origin + coords[:, :1] * U + coords[:, 1:2] * V + w * N

    rings2d = [np.asarray(poly.exterior.coords)] + [np.asarray(r.coords) for r in poly.interiors]
    faces = []
    for w, sign in ((thickness / 2.0, 1.0), (-thickness / 2.0, -1.0)):
        faces.append(Face([lift(r, w) for r in rings2d], sign * N, index, color))
    for ring in rings2d:
        top, bottom = lift(ring, thickness / 2.0), lift(ring, -thickness / 2.0)
        for k in range(len(ring) - 1):
            quad = np.array([bottom[k], bottom[k + 1], top[k + 1], top[k]])
            edge = quad[1] - quad[0]
            n = np.cross(edge, N)
            norm = np.linalg.norm(n)
            if norm < 1e-9:
                continue
            faces.append(Face([quad], n / norm, index, color))
    return faces


def _default_color(pc: Piece) -> tuple[int, int, int]:
    if pc.style == "kit" or pc.role.value in KIT_ROLES:
        return KIT_PLATE_COLOR if pc.kind == PieceKind.PROFILE else KIT_CROSS_COLOR
    return PROFILE_COLOR if pc.kind == PieceKind.PROFILE else CROSS_COLOR


KIT_ROLES = {"rib", "vertebra", "skull", "jaw", "limb", "spacer", "connector"}


def render(
    items: list[RenderItem],
    thickness: float,
    size: tuple[int, int] = (1200, 900),
    camera: Camera | None = None,
    supersample: int = 2,
    padding: float = 0.08,
    label: bool = False,
    simplify: float = 0.25,
    label_ids: set[str] | None = None,
) -> RenderResult:
    camera = camera or Camera()
    r, u, f = camera.basis()
    W, H = size[0] * supersample, size[1] * supersample
    faces = [fc for i, it in enumerate(items) for fc in _faces(it, i, thickness, simplify)]
    if not faces:
        return RenderResult(Image.new("RGB", size, BACKGROUND))

    all_pts = np.vstack([ring for fc in faces for ring in fc.rings])
    sx, sy = all_pts @ r, all_pts @ u
    span = max(sx.max() - sx.min(), sy.max() - sy.min(), 1e-6)
    scale = min(W, H) * (1 - 2 * padding) / span
    scale = min(scale, W * (1 - 2 * padding) / max(sx.max() - sx.min(), 1e-6), H * (1 - 2 * padding) / max(sy.max() - sy.min(), 1e-6))
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2

    def project(p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (p @ r - cx) * scale + W / 2, H / 2 - (p @ u - cy) * scale, p @ f

    zbuf = np.full((H, W), np.inf, dtype=np.float64)
    fid = np.full((H, W), -1, dtype=np.int32)
    color = np.zeros((H, W, 3), dtype=np.float32)
    color[:] = BACKGROUND

    for face_index, fc in enumerate(faces):
        ring0 = fc.rings[0]
        px, py, pz = project(ring0)
        # Depth plane z = a*x + b*y + c from the face normal in screen space.
        nx, ny, nz = fc.normal @ r, -(fc.normal @ u), fc.normal @ f
        if abs(nz) < 1e-6:
            continue  # face seen exactly edge-on
        x0, y0, z0 = px[0], py[0], pz[0]
        # Screen x/y are world units * scale, so the plane gradient is divided by scale.
        a = -nx / nz / scale
        b = -ny / nz / scale
        contours = []
        for ring in fc.rings:
            qx, qy, _ = project(ring)
            contours.append(np.round(np.stack([qx, qy], axis=1) * 16).astype(np.int32))
        allx = np.concatenate([c[:, 0] for c in contours]) / 16
        ally = np.concatenate([c[:, 1] for c in contours]) / 16
        bx0, bx1 = max(0, int(allx.min()) - 1), min(W, int(allx.max()) + 2)
        by0, by1 = max(0, int(ally.min()) - 1), min(H, int(ally.max()) + 2)
        if bx1 <= bx0 or by1 <= by0:
            continue
        mask = np.zeros((by1 - by0, bx1 - bx0), dtype=np.uint8)
        shifted = [c - np.array([bx0 * 16, by0 * 16], dtype=np.int32) for c in contours]
        cv2.fillPoly(mask, shifted, 1, lineType=cv2.LINE_8, shift=4)
        ys, xs = np.nonzero(mask)
        if xs.size == 0:
            continue
        gx, gy = xs + bx0, ys + by0
        depth = z0 + a * (gx - x0) + b * (gy - y0)
        closer = depth < zbuf[gy, gx]
        gx, gy, depth = gx[closer], gy[closer], depth[closer]
        zbuf[gy, gx] = depth
        fid[gy, gx] = face_index
        shade = 0.55 + 0.45 * abs(float(fc.normal @ LIGHT))
        color[gy, gx] = np.array(fc.color, dtype=np.float32) * shade

    # Outlines where the visible face changes.
    edges = np.zeros((H, W), dtype=bool)
    edges[:, 1:] |= fid[:, 1:] != fid[:, :-1]
    edges[1:, :] |= fid[1:, :] != fid[:-1, :]
    edges = cv2.dilate(edges.astype(np.uint8), np.ones((supersample, supersample), np.uint8)) > 0
    color[edges] = color[edges] * 0.35

    img = Image.fromarray(np.clip(color, 0, 255).astype(np.uint8)).resize(size, Image.LANCZOS)
    anchors = {}
    if label:
        for it in items:
            pc = it.piece
            if label_ids is not None and pc.id not in label_ids:
                continue
            c = pc.polygon.representative_point()
            p3 = np.array(pc.transform.apply(c.x, c.y, thickness / 2.0)) + np.array(it.offset)
            qx, qy, _ = project(p3[None, :])
            anchors[pc.id] = (float(qx[0]) / supersample, float(qy[0]) / supersample)
        draw_labels(img, anchors)
    return RenderResult(img, anchors)


def draw_labels(img: Image.Image, anchors: dict[str, tuple[float, float]], font_size: int = 16) -> None:
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=font_size)
    for text, (x, y) in anchors.items():
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 4
        rect = [x - tw / 2 - pad, y - th / 2 - pad, x + tw / 2 + pad, y + th / 2 + pad]
        draw.rounded_rectangle(rect, radius=5, fill=(17, 24, 39), outline=(255, 255, 255))
        draw.text((x - tw / 2 - bbox[0], y - th / 2 - bbox[1]), text, fill=(255, 255, 255), font=font)


def insertion_vector(piece: Piece, model: PuzzleModel) -> np.ndarray:
    """Display-frame direction from which a cross rib is inserted (unit vector)."""
    from app.geometry.engine import _display_vec

    f = piece.frame
    s = float(piece.slide_sign)
    return np.array(_display_vec((s * f.dx, s * f.dy, 0.0), model.orientation))


def explode_offset(piece: Piece, model: PuzzleModel, factor: float = 1.0) -> np.ndarray:
    """Exploded-view displacement: cross ribs back out along their insertion direction,
    plates move outwards along the depth axis, the spine stays."""
    w, h, d = model.object_size_mm
    if piece.kind == PieceKind.CROSS:
        return insertion_vector(piece, model) * 0.55 * max(h, 0.6 * w) * factor
    if piece.role.value == "connector":
        side = math.copysign(1.0, piece.position_mm) if piece.position_mm else 1.0
        return np.array([0.0, 0.0, side * (0.9 * abs(np.sign(piece.position_mm)) + 0.35) * d * factor])
    if piece.position_mm != 0:
        return np.array([0.0, 0.0, math.copysign(0.9 * d * factor, piece.position_mm)])
    return np.zeros(3)


def exploded_items(model: PuzzleModel, factor: float = 1.0) -> list[RenderItem]:
    return [RenderItem(pc, tuple(float(v) for v in explode_offset(pc, model, factor))) for pc in model.pieces]


def render_preview(model: PuzzleModel, size: tuple[int, int] = (1200, 900)) -> Image.Image:
    items = [RenderItem(pc) for pc in model.pieces]
    return render(items, model.params.thickness_mm, size).image


def render_mesh(mesh, size: tuple[int, int] = (900, 700), camera: Camera | None = None,
                color: tuple[int, int, int] = (205, 170, 125)) -> Image.Image:
    """Shaded preview of a triangle mesh (uploaded or reconstructed 3D model)."""
    camera = camera or Camera(30.0, 18.0)
    r, u, f = camera.basis()
    W, H = size[0] * 2, size[1] * 2
    v = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    sx, sy, sz = v @ r, v @ u, v @ f
    span = max(sx.max() - sx.min(), sy.max() - sy.min(), 1e-6)
    scale = min(W, H) * 0.84 / span
    px = (sx - (sx.max() + sx.min()) / 2) * scale + W / 2
    py = H / 2 - (sy - (sy.max() + sy.min()) / 2) * scale
    tri = v[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12
    shade = 0.45 + 0.55 * np.abs(normals @ LIGHT)
    depth = sz[faces].mean(axis=1)
    img = np.full((H, W, 3), 255, np.uint8)
    pts = np.stack([px, py], axis=1)
    base = np.array(color, np.float64)
    for i in np.argsort(-depth):  # far to near (painter's algorithm)
        poly = np.round(pts[faces[i]] * 4).astype(np.int32)
        cv2.fillConvexPoly(img, poly, tuple(int(c) for c in (base * shade[i]).clip(0, 255)), lineType=cv2.LINE_AA, shift=2)
    return Image.fromarray(img).resize(size, Image.LANCZOS)
