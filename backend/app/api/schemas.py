"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.geometry.types import Complexity, Orientation, Style

PUZZLE_SIZES_MM = {"small": 150.0, "medium": 250.0, "large": 400.0}
MATERIAL_NAMES = {"plywood": "Plywood", "mdf": "MDF", "acrylic": "Acrylic"}
OutputKind = Literal["svg", "dxf", "pdf", "assembly_guide"]
SegmentationMethod = Literal["auto", "ai", "alpha", "background", "grabcut", "threshold", "edges"]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorBody


class CreateJobRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)


class ExtractRequest(BaseModel):
    method: SegmentationMethod = "auto"


Point2 = Annotated[list[Annotated[float, Field(ge=-0.5, le=1.5)]], Field(min_length=2, max_length=2)]


class SkeletonLimbIn(BaseModel):
    kind: Literal["limb", "wing", "fin", "appendage"] = "limb"
    joints: list[Point2] = Field(min_length=2, max_length=6)


class SkeletonIn(BaseModel):
    spine: list[Point2] = Field(min_length=2, max_length=40)
    head_start: Point2 | None = None
    limbs: list[SkeletonLimbIn] = Field(default_factory=list, max_length=12)


class PuzzleSettingsIn(BaseModel):
    material: Literal["plywood", "mdf", "acrylic", "custom"] = "plywood"
    custom_material_name: str | None = Field(default=None, max_length=60)
    thickness_mm: float = Field(default=3.0, ge=0.5, le=25.0)
    kerf_mm: float = Field(default=0.15, ge=0.0, le=1.0)
    clearance_mm: float = Field(default=0.10, ge=0.0, le=1.0)
    puzzle_size: Literal["small", "medium", "large", "custom"] = "medium"
    custom_size_mm: float | None = Field(default=None, ge=40.0, le=2000.0)
    piece_count: int = Field(default=12, ge=3, le=60)
    complexity: Complexity = Complexity.MEDIUM
    orientation: Orientation = Orientation.AUTO
    labels: bool = True
    sheet_width_mm: float = Field(default=600.0, ge=50.0, le=3000.0)
    sheet_height_mm: float = Field(default=400.0, ge=50.0, le=3000.0)
    outputs: list[OutputKind] = Field(default_factory=lambda: ["svg", "dxf", "pdf", "assembly_guide"])
    # How the 3D body is obtained for photos: AI reconstruction when available (auto),
    # AI reconstruction requested explicitly, or the fast estimate from the silhouette.
    shape_source: Literal["auto", "ai_3d", "silhouette"] = "auto"
    # Construction style: filled ribs and plates, or an open skeleton (backbone, rib cage,
    # windowed plates) like a fossil model kit.
    style: Style = Style.SLICE
    # Object depth: multiplies the estimated (or reconstructed) depth of the object. 1 = as estimated.
    depth_scale: float = Field(default=1.0, ge=0.3, le=3.0)
    # Fossil kit options: arch rib count (0 = automatic), frame around the pieces on the sheet,
    # name engraved on the frame, and an override for an image judged unsuitable.
    rib_count: int = Field(default=0, ge=0, le=40)
    kit_frame: bool = False
    kit_title: str | None = Field(default=None, max_length=40)
    override_suitability: bool = False
    # Hand-edited skeleton (fossil kits): image fractions, x right / y down, as returned in
    # JobOut.suitability.skeleton. None = use the detected structure.
    skeleton: SkeletonIn | None = None

    @model_validator(mode="after")
    def _check(self) -> PuzzleSettingsIn:
        if self.puzzle_size == "custom" and self.custom_size_mm is None:
            raise ValueError("custom_size_mm is required when puzzle_size is 'custom'")
        if self.kerf_mm >= self.thickness_mm + self.clearance_mm:
            raise ValueError("kerf must be smaller than the slot width (thickness + clearance)")
        return self

    @property
    def size_mm(self) -> float:
        return self.custom_size_mm if self.puzzle_size == "custom" else PUZZLE_SIZES_MM[self.puzzle_size]

    @property
    def material_label(self) -> str:
        if self.material == "custom":
            return (self.custom_material_name or "Custom material").strip() or "Custom material"
        return MATERIAL_NAMES[self.material]


class ImageInfo(BaseModel):
    filename: str | None
    format: str | None
    width: int | None
    height: int | None
    url: str | None


class SilhouetteInfo(BaseModel):
    url: str
    method: str | None
    confidence: float | None
    low_confidence: bool
    warnings: list[str]


class FileLink(BaseModel):
    kind: str
    filename: str
    url: str
    size_bytes: int


class JobResult(BaseModel):
    piece_count: int
    material: str
    thickness_mm: float
    kerf_mm: float
    clearance_mm: float
    slot_width_nominal_mm: float
    slot_width_toolpath_mm: float
    object_width_mm: float
    object_height_mm: float
    object_depth_mm: float
    sheet_width_mm: float
    sheet_height_mm: float
    used_area_mm2: float
    waste_percentage: float
    orientation: str
    joint_count: int
    preview_url: str
    model_url: str
    sheet_svg_url: str
    files: list[FileLink]
    quality: dict = {}
    piece_roles: dict = {}
    structure: dict = {}


class JobOut(BaseModel):
    success: Literal[True] = True
    id: str
    status: str
    source_kind: str = "image"
    ai_3d_available: bool = False
    stage: str | None
    progress: int
    created_at: datetime
    updated_at: datetime
    image: ImageInfo | None
    silhouette: SilhouetteInfo | None
    settings: PuzzleSettingsIn | None
    result: JobResult | None
    warnings: list[str]
    error: ErrorBody | None
    suitability: dict | None = None  # ImageSuitabilityAnalyzer result (estimated confidences)
    structure_url: str | None = None  # detected structure drawn over the photo


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str
