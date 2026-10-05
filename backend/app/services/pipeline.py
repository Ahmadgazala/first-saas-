"""End-to-end processing pipeline, independent of the web layer and the database.

    extract_silhouette(image bytes)            -> silhouette mask + confidence + previews
    generate_puzzle(mask, parameters, ...)     -> validated geometry + every output file

Every stage either succeeds or raises PuzzleError; output files are only produced after
validation of the model, the kerf-compensated tool paths and the sheet layout has passed.
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np
from PIL import Image

from app.core.errors import ErrorCode, PuzzleError
from app.exporters.assembly_guide import export_assembly_guide
from app.exporters.dxf import export_dxf
from app.exporters.metadata import build_metadata, build_model_json
from app.exporters.pdf import export_sheet_pdf
from app.exporters.render import render_preview
from app.exporters.sheet import build_sheet
from app.exporters.svg import export_svg
from app.geometry.engine import PuzzleGenerator
from app.geometry.kit.builder import KitGenerator
from app.geometry.kit.frame import FrameGenerator
from app.geometry.kit.slice import SliceGenerator
from app.geometry.nesting import Layout, nest
from app.geometry.quality import analyze_quality
from app.geometry.normalize import fill_small_holes, normalize_silhouette
from app.geometry.slots import compensate_kerf
from app.geometry.types import PuzzleModel, PuzzleParameters, Style
from app.validators.geometry_validator import (
    ValidationReport,
    validate_layout,
    validate_model,
    validate_toolpaths,
)
from app.vision.image_io import load_image, resize_max_side, to_png_bytes
from app.vision.segmentation import segment

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]

OUTPUT_FILES = {
    "preview": ("preview.png", "image/png"),
    "svg": ("laser_sheet.svg", "image/svg+xml"),
    "dxf": ("laser_sheet.dxf", "application/dxf"),
    "pdf": ("laser_sheet.pdf", "application/pdf"),
    "assembly_guide": ("assembly_guide.pdf", "application/pdf"),
    "metadata": ("metadata.json", "application/json"),
    "model": ("model.json", "application/json"),
}
OPTIONAL_OUTPUTS = {"dxf", "pdf", "assembly_guide"}


@dataclass
class SilhouetteOutput:
    mask_png: bytes  # WHITE = object, BLACK = background
    original_png: bytes  # orientation-corrected, downscaled copy for display
    method: str
    confidence: float
    warnings: list[str]
    candidates: list[dict]
    width: int
    height: int
    suitability: dict | None = None


@dataclass
class GenerationOutput:
    files: dict[str, bytes]  # key from OUTPUT_FILES -> content
    metadata: dict
    model: PuzzleModel
    layout: Layout
    warnings: list[str] = field(default_factory=list)


def extract_silhouette(data: bytes, method: str = "auto", *, max_side: int = 8192, min_side: int = 64,
                       recommended_side: int = 4096) -> SilhouetteOutput:
    loaded = load_image(data, max_side=max_side, min_side=min_side, recommended_side=recommended_side)
    result = segment(loaded.rgba, method)
    if result.method == "none" or not (result.mask > 0).any():
        raise PuzzleError(
            ErrorCode.OBJECT_NOT_DETECTED,
            "Unable to detect the object. Please try another image with a clearer subject.",
            json.dumps(result.candidates),
        )
    display = resize_max_side(loaded.rgba, 1024)
    suitability = analyze_suitability(display, result.mask, result.confidence)
    return SilhouetteOutput(
        mask_png=to_png_bytes(result.mask),
        original_png=to_png_bytes(display),
        method=result.method,
        confidence=round(result.confidence, 3),
        warnings=loaded.warnings + result.warnings,
        candidates=result.candidates,
        width=loaded.width,
        height=loaded.height,
        suitability=suitability,
    )


def _skeleton_mm(skeleton: dict | None, mask: np.ndarray, polygon) -> dict | None:
    """User-edited skeleton (image fractions) -> mm in the frame of the silhouette polygon."""
    if not skeleton:
        return None
    from app.geometry.kit.structure import ImageFrame

    return ImageFrame(mask, polygon).skeleton_to_mm(skeleton)


def analyze_suitability(rgba: np.ndarray, mask: np.ndarray, confidence: float) -> dict:
    """ImageSuitabilityAnalyzer on the display-size image (estimated confidences)."""
    from app.vision.suitability import ImageSuitabilityAnalyzer

    return ImageSuitabilityAnalyzer().analyze(rgba, mask, confidence).to_dict()


def decode_mask(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as img:
        return (np.asarray(img.convert("L")) > 127).astype(np.uint8) * 255


def _require(report: ValidationReport, stage: str) -> None:
    if not report.ok:
        logger.error("validation failed at %s: %s", stage, report.errors)
        raise PuzzleError(
            ErrorCode.INVALID_GEOMETRY,
            "Generated geometry is invalid and could not be repaired. Try different settings.",
            f"{stage}: {report.errors[:10]}",
        )


def generate_puzzle(
    mask: np.ndarray,
    params: PuzzleParameters,
    material: str,
    segmentation: dict | None = None,
    progress: ProgressFn | None = None,
    outputs: set[str] | None = None,
    mesh=None,
    notes: list[str] | None = None,
    skeleton: dict | None = None,
) -> GenerationOutput:
    """`outputs` selects optional files (dxf, pdf, assembly_guide). The SVG, preview,
    metadata and model are always produced: the web UI previews the sheet from the SVG.

    A fossil kit whose pieces do not fit the sheet is regenerated with fewer ribs and
    vertebrae (at most three times, never below the structural minimum) before the
    PIECES_DO_NOT_FIT error is reported; the reduction is stated in the warnings."""
    if params.style not in (Style.KIT, Style.SLICE):
        return _generate(mask, params, material, segmentation, progress, outputs, mesh, notes, skeleton)
    requested = params.piece_count
    error: PuzzleError | None = None
    # Slice kits are big solid parts: allow more reduction steps before giving up on the sheet.
    for _ in range(8 if params.style == Style.SLICE else 4):
        extra = [] if params.piece_count == requested else [
            f"The kit was reduced (target {requested} -> {params.piece_count} pieces) so it fits the "
            f"{params.sheet_width_mm:g} x {params.sheet_height_mm:g} mm sheet. Use a larger sheet for more ribs "
            "and vertebrae."]
        try:
            return _generate(mask, params, material, segmentation, progress, outputs, mesh, list(notes or []) + extra,
                             skeleton)
        except PuzzleError as exc:
            if exc.code != ErrorCode.PIECES_DO_NOT_FIT:
                raise
            error = exc
            fewer = max(8 if params.style != Style.SLICE else 6, int(params.piece_count * (0.7 if params.style == Style.SLICE else 0.75)))
            if fewer >= params.piece_count:
                break
            params = replace(params, piece_count=fewer, rib_count=int(params.rib_count * 0.75))
    raise error


def _generate(
    mask: np.ndarray,
    params: PuzzleParameters,
    material: str,
    segmentation: dict | None,
    progress: ProgressFn | None,
    outputs: set[str] | None,
    mesh,
    notes: list[str] | None,
    skeleton: dict | None = None,
) -> GenerationOutput:
    wanted = OPTIONAL_OUTPUTS if outputs is None else (set(outputs) & OPTIONAL_OUTPUTS)
    def step(name: str, pct: int) -> None:
        if progress:
            progress(name, pct)

    t0 = time.perf_counter()
    if mesh is not None and params.depth_scale != 1.0:
        from app.geometry.volume import scale_depth

        mesh = scale_depth(mesh, params.depth_scale)  # user depth control on a real 3D body
    if params.style in (Style.KIT, Style.SLICE):
        # Procedural kit from the structural model (estimated or from a real 3D body).
        generator_class = SliceGenerator if params.style == Style.SLICE else KitGenerator
        step("generating", 30)
        if mesh is not None:
            from app.geometry.volume import MeshVolume

            volume = MeshVolume(mesh)
            # The mesh itself drives the cross pieces (sections), not only its outline and depth.
            model = generator_class(params).generate(volume.silhouette, depth=volume.depth,
                                                     skeleton=_skeleton_mm(skeleton, mask, volume.silhouette),
                                                     volume=volume)
            model.analysis["shape_source"] = "mesh"
            model.warnings = list(notes or []) + model.warnings
        else:
            step("normalizing", 10)
            silhouette = normalize_silhouette(mask, params.target_size_mm, params.min_feature_mm)
            step("generating", 25)
            outline = silhouette.polygon if params.style == Style.SLICE else fill_small_holes(silhouette.polygon)
            model = generator_class(params).generate(outline, skeleton=_skeleton_mm(skeleton, mask, outline))
            model.warnings = list(notes or []) + silhouette.warnings + model.warnings
    elif mesh is not None:
        # A real 3D body (AI reconstruction or uploaded model) in the display frame, in mm.
        step("generating", 40)
        model = PuzzleGenerator(params).generate_from_mesh(mesh)
        model.warnings = list(notes or []) + model.warnings
    else:
        step("normalizing", 10)
        silhouette = normalize_silhouette(mask, params.target_size_mm, params.min_feature_mm)
        step("generating", 25)
        model = PuzzleGenerator(params).generate(fill_small_holes(silhouette.polygon))
        model.warnings = list(notes or []) + silhouette.warnings + model.warnings

    step("validating", 45)
    report = validate_model(model)
    _require(report, "model")
    toolpaths = {pc.id: compensate_kerf(pc.polygon, params.kerf_mm) for pc in model.pieces}
    report.merge(validate_toolpaths(toolpaths))
    _require(report, "toolpaths")

    step("nesting", 55)
    frame = None
    margin = params.sheet_margin_mm
    if params.kit_frame:
        frame = FrameGenerator(params.thickness_mm, params.part_spacing_mm, params.sheet_margin_mm, params.kerf_mm).make(
            params.sheet_width_mm, params.sheet_height_mm, params.title or "PUZZLECUT KIT")
        if frame is None:
            model.warnings.append("The sheet is too small for a kit frame; it was left out.")
        else:
            margin = frame.inner_margin
    layout = nest(toolpaths, params.sheet_width_mm, params.sheet_height_mm, margin, params.part_spacing_mm)
    placed = {pl.piece_id: pl.polygon for pl in layout.placements}
    if frame is not None and placed:
        from shapely.ops import unary_union

        tight = FrameGenerator(params.thickness_mm, params.part_spacing_mm, params.sheet_margin_mm, params.kerf_mm).around(
            unary_union(list(placed.values())).bounds, params.sheet_width_mm, params.sheet_height_mm,
            params.title or "PUZZLECUT KIT")
        frame = tight or frame
    if frame is not None:
        placed["FRAME"] = compensate_kerf(frame.polygon, params.kerf_mm)
        report.merge(validate_toolpaths({"FRAME": placed["FRAME"]}))
    report.merge(validate_layout(placed, params.sheet_width_mm, params.sheet_height_mm,
                                 # the frame's tool path sits kerf/2 outside its nominal outline
                                 params.sheet_margin_mm - (params.kerf_mm / 2 + 1e-3 if frame else 0.0),
                                 params.part_spacing_mm))
    _require(report, "layout")

    step("exporting", 70)
    sheet = build_sheet(model, layout, frame)
    files: dict[str, bytes] = {"svg": export_svg(sheet)}
    if "dxf" in wanted:
        files["dxf"] = export_dxf(sheet)
    if "pdf" in wanted:
        files["pdf"] = export_sheet_pdf(sheet)

    step("rendering", 80)
    buf = io.BytesIO()
    render_preview(model).save(buf, format="PNG", optimize=True)
    files["preview"] = buf.getvalue()

    quality = analyze_quality(model, layout)
    if "assembly_guide" in wanted:
        step("assembly_guide", 88)
        files["assembly_guide"] = export_assembly_guide(model, layout, sheet, material, quality)

    metadata = build_metadata(model, layout, material, segmentation, report.checks, quality)
    metadata["processing_seconds"] = round(time.perf_counter() - t0, 2)
    files["metadata"] = json.dumps(metadata, indent=2).encode("utf-8")
    files["model"] = json.dumps(build_model_json(model, layout), separators=(",", ":")).encode("utf-8")
    step("completed", 100)
    return GenerationOutput(files, metadata, model, layout, model.warnings)
