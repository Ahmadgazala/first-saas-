"""Core data types of the geometry engine (framework independent).

Coordinate conventions
----------------------
Canonical puzzle frame (millimetres):
  x  - slicing axis; cross ribs are planes x = const
  y  - assembly / slide axis (up)
  z  - depth axis; profile ribs are planes z = const

Each piece has a local 2D frame (u, v) and a rigid transform into the *display* frame,
which is the canonical frame optionally rotated (horizontal slicing) and shifted so the
object stands on y = 0. The same Shapely polygons feed the exporters and the 3D preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from shapely.geometry import Polygon


class Complexity(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    ADVANCED = "advanced"


class Orientation(StrEnum):
    VERTICAL = "vertical"  # cross ribs stand vertically, slicing along the image width
    HORIZONTAL = "horizontal"  # cross ribs lie horizontally, slicing along the image height
    AUTO = "auto"


class Style(StrEnum):
    SOLID = "solid"  # filled sections: a closed, sculptural body
    SKELETON = "skeleton"  # hollowed frames: open rib cage, banded spine and skull (fossil kits)
    KIT = "kit"  # procedural fossil kit: segmented backbone, arch ribs, vertebrae, skull/jaw, limb bones
    SLICE = "slice"  # solid-slice 3D puzzle: body-shaped spine plate, dense solid cross-section slices, silhouette limbs


class PieceKind(StrEnum):
    PROFILE = "profile"  # longitudinal rib, parallel to the image plane
    CROSS = "cross"  # transverse rib, perpendicular to the image plane


class SlideDirection(StrEnum):
    # A: cross ribs carry slots opening downwards and slide down onto the profile ribs.
    CROSS_FROM_ABOVE = "cross_from_above"
    # B: cross ribs carry slots opening upwards and slide up onto the profile ribs.
    CROSS_FROM_BELOW = "cross_from_below"


@dataclass(frozen=True)
class PuzzleParameters:
    thickness_mm: float = 3.0
    kerf_mm: float = 0.15
    clearance_mm: float = 0.10
    target_size_mm: float = 250.0  # longest dimension of the assembled object's silhouette
    piece_count: int = 12
    style: Style = Style.SOLID
    complexity: Complexity = Complexity.MEDIUM
    orientation: Orientation = Orientation.AUTO
    labels: bool = True
    sheet_width_mm: float = 600.0
    sheet_height_mm: float = 400.0
    sheet_margin_mm: float = 5.0
    part_spacing_mm: float = 3.0
    rib_count: int = 0  # kit style: number of arch ribs (0 = automatic)
    kit_frame: bool = False  # surround the nested pieces with a cut frame carrying the kit name
    title: str = ""  # name engraved on the kit frame
    depth_scale: float = 1.0  # multiplies the object's depth (z): 3D mesh z-scale / estimated depth field

    @property
    def slot_width_mm(self) -> float:
        """Nominal (as-cut) slot width: material thickness plus fit clearance."""
        return round(self.thickness_mm + self.clearance_mm, 4)

    @property
    def min_wall_mm(self) -> float:
        """Minimum material left on either side of a slot."""
        return max(self.thickness_mm, 3.0)

    @property
    def min_feature_mm(self) -> float:
        """Features narrower than this are too fragile to cut and are removed."""
        return max(0.8 * self.thickness_mm, 2.5)

    @property
    def min_joint_length_mm(self) -> float:
        return max(3.0 * self.thickness_mm, 10.0)

    @property
    def min_engagement_mm(self) -> float:
        return max(self.thickness_mm, 3.0)


@dataclass
class Transform3D:
    origin: tuple[float, float, float]
    u_axis: tuple[float, float, float]
    v_axis: tuple[float, float, float]
    normal: tuple[float, float, float]

    def apply(self, u: float, v: float, w: float = 0.0) -> tuple[float, float, float]:
        o, a, b, n = self.origin, self.u_axis, self.v_axis, self.normal
        return (
            o[0] + u * a[0] + v * b[0] + w * n[0],
            o[1] + u * a[1] + v * b[1] + w * n[1],
            o[2] + u * a[2] + v * b[2] + w * n[2],
        )


@dataclass(frozen=True)
class RibFrame:
    """Plane of a cross rib, which always contains the depth (z) axis.

    The rib's trace in the canonical xy plane is the line c + t*d. Local rib coordinates are
    (s, t): s = n·(p - c) across the rib, t = d·(p - c) along it, with n = (dy, -dx).
    A vertical rib at x0 is RibFrame(x0, 0, 0, 1): s = x - x0, t = y.
    """

    cx: float
    cy: float
    dx: float
    dy: float

    @property
    def n(self) -> tuple[float, float]:
        return (self.dy, -self.dx)

    @property
    def angle_deg(self) -> float:
        """Tilt of the rib from vertical (0 = vertical rib)."""
        import math

        return math.degrees(math.atan2(-self.dx, self.dy))

    def point(self, t: float) -> tuple[float, float]:
        return (self.cx + t * self.dx, self.cy + t * self.dy)

    def to_local(self, geom):
        from shapely import affinity

        nx, ny = self.n
        return affinity.affine_transform(
            geom, [nx, ny, self.dx, self.dy, -(nx * self.cx + ny * self.cy), -(self.dx * self.cx + self.dy * self.cy)]
        )

    def to_world(self, geom):
        from shapely import affinity

        nx, ny = self.n
        return affinity.affine_transform(geom, [nx, self.dx, ny, self.dy, self.cx, self.cy])


@dataclass
class Slot:
    """A slot in a piece's local frame: a channel of `width_mm` that starts at `origin` (the
    centre of the closed end) and runs along `direction` through the piece edge."""

    joint_id: str
    origin: tuple[float, float]
    direction: tuple[float, float]  # unit vector from the slot bottom towards the open mouth
    depth_mm: float  # slot bottom -> piece edge
    width_mm: float

    def channel(self, length: float | None = None, inset: float = 0.0, extra_width: float = 0.0):
        """Rectangle of the slot from `inset` past the bottom to `length` (default: its depth)."""
        from shapely.geometry import Polygon

        L = self.depth_mm if length is None else length
        ox, oy = self.origin
        dx, dy = self.direction
        nx, ny = -dy, dx
        h = self.width_mm / 2.0 + extra_width
        a0, a1 = inset, L
        return Polygon([
            (ox + dx * a0 + nx * h, oy + dy * a0 + ny * h),
            (ox + dx * a1 + nx * h, oy + dy * a1 + ny * h),
            (ox + dx * a1 - nx * h, oy + dy * a1 - ny * h),
            (ox + dx * a0 - nx * h, oy + dy * a0 - ny * h),
        ])


class PieceRole(StrEnum):
    SPINE = "spine"  # central backbone profile (z = 0)
    PROFILE = "profile"  # longitudinal torso profile at ±z
    DETAIL = "detail"  # localized plate (head, neck, hip, ...) at ±z
    CROSS = "cross"  # transverse rib
    # Fossil-kit components
    RIB = "rib"  # open arch rib hanging from the backbone
    VERTEBRA = "vertebra"  # cross plate with neural spine on the backbone
    SKULL = "skull"  # skull side plate
    JAW = "jaw"  # lower jaw side plate
    LIMB = "limb"  # limb / wing / fin bone segment
    SPACER = "spacer"  # beam joining parallel plates (skull, jaw, girdles)
    CONNECTOR = "connector"  # bowtie key joining two coplanar plates


@dataclass
class Piece:
    id: str
    kind: PieceKind
    polygon: Polygon  # nominal (design) geometry in local (u, v) mm
    transform: Transform3D | None = None
    position_mm: float = 0.0  # z for profile pieces; for cross ribs see `frame`
    slots: list[Slot] = field(default_factory=list)
    engrave: list[list[tuple[float, float]]] = field(default_factory=list)  # local polylines
    label_height_mm: float = 0.0
    label: str = ""  # engraved text (the ID, or a short code when the ID does not fit)
    role: PieceRole = PieceRole.CROSS
    region: str = "body"  # anatomical region the piece mainly describes
    frame: RibFrame | None = None  # cross ribs only
    slide_sign: int = 1  # cross ribs: +1 inserted from +d, -1 from -d (canonical frame)
    style: str = ""  # "kit" for fossil-kit components (rendered in white material)


@dataclass
class Joint:
    id: str
    profile_id: str
    cross_id: str
    x_mm: float  # canonical intersection point of the two planes (on the cross rib's trace)
    y_mm: float
    z_mm: float
    t_low: float  # shared interval of both pieces along the cross rib's trace
    t_high: float
    t_split: float  # slots meet here
    engagement_mm: float
    load_bearing: bool


@dataclass
class PuzzleModel:
    params: PuzzleParameters
    orientation: Orientation  # resolved (never AUTO)
    slide: SlideDirection
    pieces: list[Piece]
    joints: list[Joint]
    silhouette: Polygon  # display-frame silhouette used as the outer boundary (mm)
    object_size_mm: tuple[float, float, float]  # width, height, depth in the display frame
    warnings: list[str] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)  # region summary and structural statistics

    def piece(self, piece_id: str) -> Piece:
        for p in self.pieces:
            if p.id == piece_id:
                return p
        raise KeyError(piece_id)
