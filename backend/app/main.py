"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.errors import ErrorCode, PuzzleError
from app.core.logging import configure_logging
from app.models.db import Database
from app.services.job_service import JobService
from app.services.storage import LocalStorageService
from app.services.task_queue import TaskQueue, ThreadPoolTaskQueue

logger = logging.getLogger("puzzlecut")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": {"code": code, "message": message}})


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(x) for x in err.get("loc", []) if x not in ("body", "query", "path"))
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "Invalid request: " + "; ".join(parts)


def create_app(settings: Settings | None = None, queue: TaskQueue | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    storage = LocalStorageService(settings.storage_dir)
    task_queue = queue or ThreadPoolTaskQueue(settings.worker_threads)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.create_all()
        logger.info("PuzzleCut API ready (storage=%s)", storage.root)
        yield
        task_queue.shutdown()

    app = FastAPI(title="PuzzleCut AI API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.storage = storage
    app.state.jobs = JobService(db, storage, task_queue, settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    @app.exception_handler(PuzzleError)
    async def puzzle_error(_: Request, exc: PuzzleError):
        log = logger.error if exc.http_status >= 500 else logger.info
        log("request failed: %s", exc)
        return _error(exc.http_status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return _error(422, ErrorCode.VALIDATION_ERROR, _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.JOB_NOT_FOUND if exc.status_code == 404 else ErrorCode.VALIDATION_ERROR
        message = "Not found." if exc.status_code == 404 else str(exc.detail)
        return _error(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        logger.exception("unhandled error", exc_info=exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Something went wrong on our side. Please try again.")

    app.include_router(router)
    return app

