"""Job lifecycle: create -> upload (silhouette extraction) -> generate (background) -> files."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import replace

import numpy as np

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import PuzzleSettingsIn
from app.core.config import Settings
from app.core.errors import ErrorCode, PuzzleError
from app.geometry.types import PuzzleParameters
from app.models.db import Database
from app.models.entities import GeneratedFile, JobStatus, Project, PuzzleJob, PuzzleSettings
from app.services.pipeline import OUTPUT_FILES, decode_mask, extract_silhouette, generate_puzzle
from app.services.storage import StorageService
from app.services.task_queue import TaskQueue
from app.validators.file_validators import (
    sanitize_filename,
    sniff_image_type,
    sniff_model_type,
    validate_upload_size,
)
from app.vision.segmentation import LOW_CONFIDENCE

logger = logging.getLogger(__name__)


_MODEL_SUITABILITY = {
    "suitable": True, "overall_confidence": 1.0, "silhouette_confidence": 1.0, "structure_confidence": 1.0,
    "recommended_view": "any", "scores": {}, "warnings": [], "blocking": [], "guidance": None,
    "structure": None, "confidence_kind": "3d-model",
}


def parse_job_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(job_id))
    except (ValueError, AttributeError, TypeError):
        raise PuzzleError(ErrorCode.JOB_NOT_FOUND, "Job not found.", f"malformed id {job_id!r}") from None


def to_parameters(s: PuzzleSettingsIn) -> PuzzleParameters:
    return PuzzleParameters(
        thickness_mm=s.thickness_mm,
        kerf_mm=s.kerf_mm,
        clearance_mm=s.clearance_mm,
        target_size_mm=s.size_mm,
        piece_count=s.piece_count,
        complexity=s.complexity,
        orientation=s.orientation,
        labels=s.labels,
        sheet_width_mm=s.sheet_width_mm,
        sheet_height_mm=s.sheet_height_mm,
        style=s.style,
        rib_count=s.rib_count,
        kit_frame=s.kit_frame,
        title=(s.kit_title or "").strip(),
        depth_scale=s.depth_scale,
    )


class JobService:
    def __init__(self, db: Database, storage: StorageService, queue: TaskQueue, settings: Settings):
        self.db = db
        self.storage = storage
        self.queue = queue
        self.settings = settings

    # ------------------------------------------------------------------ queries

    def get(self, session: Session, job_id: str) -> PuzzleJob:
        job = session.execute(
            select(PuzzleJob)
            .where(PuzzleJob.id == parse_job_id(job_id))
            .options(selectinload(PuzzleJob.settings), selectinload(PuzzleJob.files))
        ).scalar_one_or_none()
        if job is None:
            raise PuzzleError(ErrorCode.JOB_NOT_FOUND, "Job not found.", job_id)
        return job

    def create(self, session: Session, name: str | None = None) -> PuzzleJob:
        project = Project(name=(name or "Untitled puzzle").strip()[:200] or "Untitled puzzle")
        session.add(project)
        session.flush()
        job = PuzzleJob(project_id=project.id, status=JobStatus.CREATED, warnings=[], segmentation_warnings=[])
        session.add(job)
        session.flush()
        return job

    # ------------------------------------------------------------------ upload / silhouette

    def upload(self, session: Session, job_id: str, filename: str | None, data: bytes) -> PuzzleJob:
        job = self.get(session, job_id)
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "This job is currently being generated.")
        model_ext = sniff_model_type(data, filename)
        if model_ext is not None:
            validate_upload_size(len(data), self.settings.max_model_upload_mb * 1024 * 1024)
            return self._upload_model(session, job, filename, data, model_ext)
        validate_upload_size(len(data), self.settings.max_upload_bytes)
        sniffed = sniff_image_type(data)
        sil = self._extract(data, "auto")
        image_key = f"uploads/{job.id}/original.{sniffed.extension}"
        self.storage.save_bytes(image_key, data)
        self._clear_outputs(session, job)
        job.original_filename = sanitize_filename(filename)
        job.image_key = image_key
        job.image_format = sniffed.format
        job.image_width, job.image_height = sil.width, sil.height
        job.source_kind = "image"
        self._store_silhouette(job, sil)
        self._forget_reconstruction(job)
        job.status = JobStatus.UPLOADED
        return job

    def _upload_model(self, session: Session, job: PuzzleJob, filename: str | None, data: bytes, ext: str):
        """A 3D model (STL / OBJ / GLB / PLY) is repaired, oriented and sliced directly."""
        import io as _io

        from app.exporters.render import render_mesh
        from app.geometry.volume import (
            MeshVolume,
            load_mesh_bytes,
            orient_uploaded_model,
            prepare_mesh,
            remesh,
            silhouette_mask_png,
        )
        from app.reconstruction.service import mesh_to_bytes
        from app.vision.image_io import to_png_bytes

        clean = remesh(load_mesh_bytes(data, ext))
        rotation = orient_uploaded_model(clean, ext)
        oriented = prepare_mesh(clean, rotation, 250.0)
        mask = silhouette_mask_png(MeshVolume(oriented))
        preview = render_mesh(oriented)
        buf = _io.BytesIO()
        preview.save(buf, format="PNG")
        self.storage.save_bytes(f"uploads/{job.id}/original.{ext}", data)
        self.storage.save_bytes(f"uploads/{job.id}/model.ply", mesh_to_bytes(oriented))
        self._clear_outputs(session, job)
        self._forget_reconstruction(job)
        job.original_filename = sanitize_filename(filename)
        job.image_key = f"uploads/{job.id}/original.{ext}"
        job.image_format = ext.upper()
        job.image_width = job.image_height = None
        job.source_kind = "model"
        job.silhouette_key = f"uploads/{job.id}/silhouette.png"
        job.original_preview_key = f"uploads/{job.id}/original_preview.png"
        self.storage.save_bytes(job.silhouette_key, to_png_bytes(mask))
        self.storage.save_bytes(job.original_preview_key, buf.getvalue())
        job.segmentation_method = "3d-model"
        job.segmentation_confidence = 1.0
        job.segmentation_warnings = []
        job.segmentation_candidates = []
        job.suitability = _MODEL_SUITABILITY
        job.status = JobStatus.UPLOADED
        return job

    def _forget_reconstruction(self, job: PuzzleJob) -> None:
        key = f"uploads/{job.id}/reconstruction.ply"
        path = self.storage.local_path(key)
        if path is not None and path.is_file():
            path.unlink()

    def reextract(self, session: Session, job_id: str, method: str) -> PuzzleJob:
        job = self.get(session, job_id)
        if job.image_key is None:
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "Upload an image first.")
        if job.source_kind == "model":
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "Detection methods apply to photos, not to 3D models.")
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "This job is currently being generated.")
        sil = self._extract(self.storage.read_bytes(job.image_key), method)
        self._clear_outputs(session, job)
        self._store_silhouette(job, sil)
        self._forget_reconstruction(job)
        job.status = JobStatus.UPLOADED
        return job

    def _extract(self, data: bytes, method: str):
        s = self.settings
        return extract_silhouette(
            data, method, max_side=s.max_image_side_px, min_side=s.min_image_side_px,
            recommended_side=s.recommended_image_side_px,
        )

    def _store_silhouette(self, job: PuzzleJob, sil) -> None:
        job.silhouette_key = f"uploads/{job.id}/silhouette.png"
        job.original_preview_key = f"uploads/{job.id}/original_preview.png"
        self.storage.save_bytes(job.silhouette_key, sil.mask_png)
        self.storage.save_bytes(job.original_preview_key, sil.original_png)
        job.segmentation_method = sil.method
        job.segmentation_confidence = sil.confidence
        job.segmentation_warnings = sil.warnings
        job.segmentation_candidates = sil.candidates
        job.suitability = sil.suitability
        self._store_structure(job, sil.original_png, sil.mask_png)

    def _store_structure(self, job: PuzzleJob, original_png: bytes, mask_png: bytes) -> None:
        from app.vision.suitability import structure_overlay

        key = f"uploads/{job.id}/structure.png"
        png = structure_overlay(original_png, mask_png)
        if png is not None:
            self.storage.save_bytes(key, png)

    def analyze(self, session: Session, job_id: str) -> PuzzleJob:
        """Re-run the image suitability and structure analysis on the stored silhouette."""
        import io as _io

        from PIL import Image

        from app.services.pipeline import analyze_suitability

        job = self.get(session, job_id)
        if job.silhouette_key is None:
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "Upload an image first.")
        if job.source_kind == "model":
            job.suitability = _MODEL_SUITABILITY
            return job
        original = self.storage.read_bytes(job.original_preview_key)
        mask_png = self.storage.read_bytes(job.silhouette_key)
        with Image.open(_io.BytesIO(original)) as im:
            rgba = np.asarray(im.convert("RGBA"))
        job.suitability = analyze_suitability(rgba, decode_mask(mask_png), job.segmentation_confidence or 0.0)
        self._store_structure(job, original, mask_png)
        return job

    def _clear_outputs(self, session: Session, job: PuzzleJob) -> None:
        for f in list(job.files):
            session.delete(f)
        job.files = []
        self.storage.delete_prefix(f"generated/{job.id}")
        job.result, job.error_code, job.error_message = None, None, None
        job.warnings, job.stage, job.progress = [], None, 0

    # ------------------------------------------------------------------ generation

    def start_generation(self, session: Session, job_id: str, settings_in: PuzzleSettingsIn) -> PuzzleJob:
        job = self.get(session, job_id)
        if job.silhouette_key is None:
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "Upload an image before generating a puzzle.")
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            raise PuzzleError(ErrorCode.JOB_STATE_CONFLICT, "This job is already being generated.")
        to_parameters(settings_in)  # fail fast on inconsistent settings
        suit = job.suitability or {}
        if job.source_kind != "model" and suit and not suit.get("suitable", True) and not settings_in.override_suitability:
            reasons = "; ".join(suit.get("blocking") or suit.get("warnings") or [])
            raise PuzzleError(
                ErrorCode.IMAGE_UNSUITABLE,
                (suit.get("guidance") or "This image is not suitable for a structural kit.")
                + (f" ({reasons})" if reasons else "")
                + " You can override this check and generate anyway.",
                f"estimated confidence {suit.get('overall_confidence')}",
            )
        if job.settings is None:
            job.settings = PuzzleSettings(job_id=job.id, **self._settings_columns(settings_in))
        else:
            for k, v in self._settings_columns(settings_in).items():
                setattr(job.settings, k, v)
        self._clear_outputs(session, job)
        job.status, job.stage, job.progress = JobStatus.QUEUED, "queued", 0
        session.flush()
        session.commit()
        self.queue.submit(self.run_generation, job.id)
        return job

    @staticmethod
    def _settings_columns(s: PuzzleSettingsIn) -> dict:
        return {
            "material": s.material if s.material != "custom" else f"custom:{s.material_label}",
            "thickness_mm": s.thickness_mm,
            "kerf_mm": s.kerf_mm,
            "clearance_mm": s.clearance_mm,
            "puzzle_size_preset": s.puzzle_size,
            "puzzle_size_mm": s.size_mm,
            "piece_count": s.piece_count,
            "complexity": s.complexity.value,
            "orientation": s.orientation.value,
            "labels": s.labels,
            "sheet_width_mm": s.sheet_width_mm,
            "sheet_height_mm": s.sheet_height_mm,
            "outputs": list(s.outputs),
            "shape_source": s.shape_source,
            "style": s.style.value,
            "depth_scale": s.depth_scale,
            "rib_count": s.rib_count,
            "kit_frame": s.kit_frame,
            "kit_title": s.kit_title,
            "override_suitability": s.override_suitability,
            "skeleton": s.skeleton.model_dump() if s.skeleton else None,
        }

    def run_generation(self, job_id: str) -> None:
        """Executed by the task queue, outside any HTTP request."""
        try:
            with self.db.session() as session:
                job = self.get(session, job_id)
                job.status, job.stage, job.progress = JobStatus.PROCESSING, "starting", 5
                settings_in = settings_from_row(job.settings)
                mask = decode_mask(self.storage.read_bytes(job.silhouette_key))
                segmentation = {"method": job.segmentation_method, "confidence": job.segmentation_confidence}
                source = {"kind": job.source_kind, "image_key": job.image_key, "id": job.id}
                stem = (job.original_filename or "").rsplit(".", 1)[0].replace("_", " ").replace("-", " ")

            def progress(stage: str, pct: int) -> None:
                with self.db.session() as s:
                    j = s.get(PuzzleJob, job_id)
                    j.stage, j.progress = stage, pct

            params = to_parameters(settings_in)
            if params.kit_frame and not params.title:
                params = replace(params, title=stem or "PuzzleCut kit")
            mesh, notes = self._body_mesh(source, settings_in, mask, progress)
            run = lambda m, n: generate_puzzle(  # noqa: E731
                mask, params, settings_in.material_label, segmentation, progress,
                outputs=set(settings_in.outputs), mesh=m, notes=n,
                skeleton=settings_in.skeleton.model_dump() if settings_in.skeleton else None,
            )
            try:
                output = run(mesh, notes)
            except PuzzleError as exc:
                if mesh is None or source["kind"] == "model" or exc.code != ErrorCode.INVALID_GEOMETRY:
                    raise
                logger.warning("slicing the AI reconstruction failed for %s: %s", job_id, exc)
                output = run(None, ["The AI 3D shape could not be sliced cleanly; the body was estimated "
                                    "from the silhouette instead."])
            with self.db.session() as session:
                job = self.get(session, job_id)
                for kind, content in output.files.items():
                    filename, ctype = OUTPUT_FILES[kind]
                    key = f"generated/{job.id}/{filename}"
                    self.storage.save_bytes(key, content)
                    session.add(GeneratedFile(
                        job_id=job.id, kind=kind, storage_key=key, filename=filename, content_type=ctype,
                        size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    ))
                job.result = output.metadata
                job.warnings = output.warnings
                job.status, job.stage, job.progress = JobStatus.COMPLETED, "completed", 100
        except PuzzleError as exc:
            logger.warning("job %s failed: %s", job_id, exc)
            self._fail(job_id, exc.code, exc.message)
        except Exception:
            logger.exception("job %s crashed", job_id)
            self._fail(job_id, ErrorCode.INTERNAL_ERROR, "Puzzle generation failed unexpectedly. Please try again.")

    def _body_mesh(self, source: dict, settings_in: PuzzleSettingsIn, mask, progress) -> tuple:
        """The real 3D body for this job (uploaded model or AI reconstruction), or None to
        estimate it from the silhouette. Returns (mesh in mm, notes for the user)."""
        from app.geometry.volume import prepare_mesh
        from app.reconstruction.service import (
            MIN_ALIGNMENT_IOU,
            MIN_DEPTH_RATIO,
            canonical_from_reconstruction,
            depth_ratio,
            get_reconstructor,
            mesh_from_bytes,
            mesh_to_bytes,
        )
        from app.vision.image_io import load_image, resize_max_side

        size = settings_in.size_mm
        if source["kind"] == "model":
            progress("loading_model", 15)
            oriented = mesh_from_bytes(self.storage.read_bytes(f"uploads/{source['id']}/model.ply"))
            return prepare_mesh(oriented, np.eye(3), size), ["Pieces are exact sections of the uploaded 3D model."]
        if settings_in.shape_source == "silhouette":
            return None, []
        rec = get_reconstructor(self.settings)
        if rec is None:
            if settings_in.shape_source == "ai_3d":
                return None, ["AI 3D reconstruction is not available on this server; the body was estimated "
                              "from the silhouette."]
            return None, []
        key = f"uploads/{source['id']}/reconstruction.ply"
        try:
            if self.storage.exists(key):
                raw = mesh_from_bytes(self.storage.read_bytes(key))
            else:
                progress("reconstructing_3d", 12)
                rgba = resize_max_side(load_image(self.storage.read_bytes(source["image_key"])).rgba, 768)
                raw = rec.reconstruct(rgba, mask)
                self.storage.save_bytes(key, mesh_to_bytes(raw))
            progress("aligning_3d", 30)
            mesh, iou = canonical_from_reconstruction(raw, mask, size)
        except Exception:
            logger.exception("AI 3D reconstruction failed for job %s", source["id"])
            return None, ["AI 3D reconstruction failed for this photo; the body was estimated from the silhouette."]
        if depth_ratio(mesh) < MIN_DEPTH_RATIO:
            return None, ["This image looks like flat artwork, so the AI 3D shape came out as a thin slab; the "
                          "body was estimated from the silhouette instead."]
        if iou < MIN_ALIGNMENT_IOU:
            return None, [f"The AI 3D shape did not match the photo well ({iou:.0%} overlap); the body was "
                          "estimated from the silhouette instead."]
        return mesh, [f"3D shape reconstructed by AI from the photo ({iou:.0%} silhouette match). The hidden "
                      "side is inferred by the model."]

    def _fail(self, job_id: str, code: ErrorCode, message: str) -> None:
        try:
            with self.db.session() as session:
                job = session.get(PuzzleJob, job_id)
                if job is not None:
                    job.status, job.stage = JobStatus.FAILED, "failed"
                    job.error_code, job.error_message = str(code), message
        except Exception:
            logger.exception("could not record failure for job %s", job_id)

    # ------------------------------------------------------------------ files

    def file_for(self, job: PuzzleJob, kind: str) -> GeneratedFile:
        if job.status != JobStatus.COMPLETED:
            raise PuzzleError(ErrorCode.FILE_NOT_READY, "The puzzle has not been generated yet.")
        for f in job.files:
            if f.kind == kind:
                return f
        raise PuzzleError(ErrorCode.FILE_NOT_READY, "This file was not generated for this job.", kind)


def settings_from_row(row: PuzzleSettings) -> PuzzleSettingsIn:
    material = row.material
    custom_name = None
    if material.startswith("custom:"):
        material, custom_name = "custom", material.split(":", 1)[1]
    return PuzzleSettingsIn(
        material=material,
        custom_material_name=custom_name,
        thickness_mm=row.thickness_mm,
        kerf_mm=row.kerf_mm,
        clearance_mm=row.clearance_mm,
        puzzle_size=row.puzzle_size_preset or "custom",
        custom_size_mm=row.puzzle_size_mm if (row.puzzle_size_preset or "custom") == "custom" else None,
        piece_count=row.piece_count,
        complexity=row.complexity,
        orientation=row.orientation,
        labels=row.labels,
        sheet_width_mm=row.sheet_width_mm,
        sheet_height_mm=row.sheet_height_mm,
        outputs=row.outputs or ["svg", "dxf", "pdf", "assembly_guide"],
        shape_source=row.shape_source or "auto",
        style=row.style or "solid",
        depth_scale=row.depth_scale if row.depth_scale else 1.0,
        rib_count=row.rib_count or 0,
        kit_frame=bool(row.kit_frame),
        kit_title=row.kit_title,
        override_suitability=bool(row.override_suitability),
        skeleton=row.skeleton,
    )


def is_low_confidence(confidence: float | None) -> bool:
    return confidence is not None and confidence < LOW_CONFIDENCE
