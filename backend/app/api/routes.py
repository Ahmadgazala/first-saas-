"""REST endpoints. Thin layer: validation and serialization only; logic lives in services."""

from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import text

from app.api.schemas import (
    CreateJobRequest,
    ExtractRequest,
    FileLink,
    HealthOut,
    ImageInfo,
    JobOut,
    JobResult,
    PuzzleSettingsIn,
    SilhouetteInfo,
)
from app.core.errors import ErrorCode, PuzzleError
from app.exporters.metadata import GENERATOR_VERSION
from app.models.entities import JobStatus, PuzzleJob
from app.services.job_service import JobService, is_low_confidence, settings_from_row

router = APIRouter(prefix="/api")

DOWNLOAD_KINDS = {"svg": "svg", "dxf": "dxf", "pdf": "pdf", "assembly-guide": "assembly_guide"}
UPLOAD_CHUNK = 1024 * 1024


def _service(request: Request) -> JobService:
    return request.app.state.jobs


def _ai(request: Request) -> bool:
    from app.reconstruction.service import get_reconstructor

    return get_reconstructor(request.app.state.settings) is not None


def serialize(job: PuzzleJob, ai_available: bool = False) -> JobOut:
    base = f"/api/jobs/{job.id}"
    image = None
    if job.image_key:
        image = ImageInfo(
            filename=job.original_filename, format=job.image_format, width=job.image_width,
            height=job.image_height, url=f"{base}/original",
        )
    silhouette = None
    if job.silhouette_key:
        silhouette = SilhouetteInfo(
            url=f"{base}/silhouette",
            method=job.segmentation_method,
            confidence=job.segmentation_confidence,
            low_confidence=is_low_confidence(job.segmentation_confidence),
            warnings=job.segmentation_warnings or [],
        )
    result = None
    if job.status == JobStatus.COMPLETED and job.result:
        m = job.result
        files = [
            FileLink(kind=f.kind, filename=f.filename, url=f"{base}/download/{f.kind.replace('_', '-')}",
                     size_bytes=f.size_bytes)
            for f in sorted(job.files, key=lambda f: f.kind)
            if f.kind.replace("_", "-") in DOWNLOAD_KINDS
        ]
        result = JobResult(
            piece_count=m["piece_count"], material=m["material"], thickness_mm=m["thickness_mm"],
            kerf_mm=m["kerf_mm"], clearance_mm=m["clearance_mm"],
            slot_width_nominal_mm=m["slot_width_nominal_mm"], slot_width_toolpath_mm=m["slot_width_toolpath_mm"],
            object_width_mm=m["object_width_mm"], object_height_mm=m["object_height_mm"],
            object_depth_mm=m["object_depth_mm"], sheet_width_mm=m["sheet_width_mm"],
            sheet_height_mm=m["sheet_height_mm"], used_area_mm2=m["used_area_mm2"],
            waste_percentage=m["waste_percentage"], orientation=m["orientation"], joint_count=m["joint_count"],
            preview_url=f"{base}/preview", model_url=f"{base}/model", sheet_svg_url=f"{base}/sheet.svg",
            files=files, quality=m.get("quality", {}), piece_roles=m.get("piece_roles", {}),
            structure={k: v for k, v in m.get("structure", {}).items() if k != "plates"},
        )
    error = {"code": job.error_code, "message": job.error_message} if job.error_code else None
    return JobOut(
        id=job.id, status=job.status, stage=job.stage, progress=job.progress,
        source_kind=job.source_kind or "image", ai_3d_available=ai_available,
        created_at=job.created_at, updated_at=job.updated_at, image=image, silhouette=silhouette,
        settings=settings_from_row(job.settings) if job.settings else None, result=result,
        warnings=job.warnings or [],
        error=error,
        suitability=job.suitability,
        structure_url=f"{base}/structure" if job.silhouette_key and job.source_kind != "model" else None,
    )


@router.get("/health", response_model=HealthOut)
def health(request: Request) -> HealthOut:
    try:
        with request.app.state.db.session() as s:
            s.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "unavailable"
    return HealthOut(status="ok" if db == "ok" else "degraded", database=db, version=GENERATOR_VERSION)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(request: Request, body: CreateJobRequest | None = None) -> JobOut:
    with request.app.state.db.session() as s:
        job = _service(request).create(s, body.name if body else None)
        s.flush()
        return serialize(_service(request).get(s, job.id), _ai(request))


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(request: Request, job_id: str) -> JobOut:
    with request.app.state.db.session() as s:
        return serialize(_service(request).get(s, job_id), _ai(request))


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks, total = [], 0
    while chunk := await upload.read(UPLOAD_CHUNK):
        total += len(chunk)
        if total > limit:
            raise PuzzleError(
                ErrorCode.FILE_TOO_LARGE, f"The file is too large. Maximum upload size is {limit // (1024 * 1024)} MB."
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/jobs/{job_id}/upload", response_model=JobOut)
async def upload_image(request: Request, job_id: str, file: UploadFile = File(...)) -> JobOut:
    svc = _service(request)
    limit = max(svc.settings.max_upload_bytes, svc.settings.max_model_upload_mb * 1024 * 1024)
    data = await _read_limited(file, limit)
    with request.app.state.db.session() as s:
        job = svc.upload(s, job_id, file.filename, data)
        s.flush()
        return serialize(job, _ai(request))


@router.post("/jobs/{job_id}/silhouette", response_model=JobOut)
def reextract(request: Request, job_id: str, body: ExtractRequest) -> JobOut:
    with request.app.state.db.session() as s:
        job = _service(request).reextract(s, job_id, body.method)
        s.flush()
        return serialize(job, _ai(request))


@router.post("/jobs/{job_id}/analyze", response_model=JobOut)
def analyze(request: Request, job_id: str) -> JobOut:
    """Image suitability + structural analysis of the uploaded image (no geometry yet)."""
    with request.app.state.db.session() as s:
        job = _service(request).analyze(s, job_id)
        s.flush()
        return serialize(job, _ai(request))


@router.post("/jobs/{job_id}/generate", response_model=JobOut, status_code=202)
def generate(request: Request, job_id: str, body: PuzzleSettingsIn) -> JobOut:
    svc = _service(request)
    with request.app.state.db.session() as s:
        svc.start_generation(s, job_id, body)
    with request.app.state.db.session() as s:
        return serialize(svc.get(s, job_id), _ai(request))


def _stored(request: Request, key: str | None, media_type: str, filename: str | None = None, inline=True):
    if not key:
        raise PuzzleError(ErrorCode.FILE_NOT_READY, "The requested file does not exist.")
    storage = request.app.state.storage
    path = storage.local_path(key)
    disposition = "inline" if inline else "attachment"
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if path is not None:
        if not path.is_file():
            raise PuzzleError(ErrorCode.FILE_NOT_READY, "The requested file does not exist.", key)
        return FileResponse(path, media_type=media_type, filename=filename, content_disposition_type=disposition,
                            headers=headers)
    data = storage.read_bytes(key)
    if filename:
        headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return Response(data, media_type=media_type, headers=headers)


@router.get("/jobs/{job_id}/original")
def original(request: Request, job_id: str):
    with request.app.state.db.session() as s:
        key = _service(request).get(s, job_id).original_preview_key
    return _stored(request, key, "image/png")


@router.get("/jobs/{job_id}/structure")
def structure(request: Request, job_id: str):
    with request.app.state.db.session() as s:
        job = _service(request).get(s, job_id)
        key = f"uploads/{job.id}/structure.png" if job.silhouette_key else None
    return _stored(request, key, "image/png")


@router.get("/jobs/{job_id}/silhouette")
def silhouette(request: Request, job_id: str):
    with request.app.state.db.session() as s:
        key = _service(request).get(s, job_id).silhouette_key
    return _stored(request, key, "image/png")


def _generated(request: Request, job_id: str, kind: str, inline: bool):
    svc = _service(request)
    with request.app.state.db.session() as s:
        f = svc.file_for(svc.get(s, job_id), kind)
        key, ctype, name = f.storage_key, f.content_type, f.filename
    return _stored(request, key, ctype, None if inline else f"puzzlecut_{job_id[:8]}_{name}", inline)


@router.get("/jobs/{job_id}/preview")
def preview(request: Request, job_id: str):
    return _generated(request, job_id, "preview", inline=True)


@router.get("/jobs/{job_id}/model")
def model(request: Request, job_id: str):
    return _generated(request, job_id, "model", inline=True)


@router.get("/jobs/{job_id}/metadata")
def metadata(request: Request, job_id: str):
    return _generated(request, job_id, "metadata", inline=True)


@router.get("/jobs/{job_id}/sheet.svg")
def sheet_svg(request: Request, job_id: str):
    return _generated(request, job_id, "svg", inline=True)


@router.get("/jobs/{job_id}/download/{kind}")
def download(request: Request, job_id: str, kind: str):
    if kind not in DOWNLOAD_KINDS:
        raise PuzzleError(ErrorCode.FILE_NOT_READY, "Unknown file type.", kind)
    return _generated(request, job_id, DOWNLOAD_KINDS[kind], inline=False)
