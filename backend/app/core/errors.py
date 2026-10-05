"""Domain errors with stable codes and user-facing messages.

Every error that can reach the API is a PuzzleError. Technical details go to the
logs via `detail`; only `code` and `message` are ever sent to clients.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_FILE = "INVALID_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    INVALID_RESOLUTION = "INVALID_RESOLUTION"
    OBJECT_NOT_DETECTED = "OBJECT_NOT_DETECTED"
    INVALID_SETTINGS = "INVALID_SETTINGS"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    TOO_MANY_PIECES = "TOO_MANY_PIECES"
    PIECES_DO_NOT_FIT = "PIECES_DO_NOT_FIT"
    EXPORT_FAILED = "EXPORT_FAILED"
    DXF_EXPORT_FAILED = "DXF_EXPORT_FAILED"
    SVG_EXPORT_FAILED = "SVG_EXPORT_FAILED"
    PDF_EXPORT_FAILED = "PDF_EXPORT_FAILED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_STATE_CONFLICT = "JOB_STATE_CONFLICT"
    FILE_NOT_READY = "FILE_NOT_READY"
    STORAGE_ERROR = "STORAGE_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    IMAGE_UNSUITABLE = "IMAGE_UNSUITABLE"


HTTP_STATUS = {
    ErrorCode.INVALID_FILE: 400,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.INVALID_RESOLUTION: 400,
    ErrorCode.OBJECT_NOT_DETECTED: 422,
    ErrorCode.INVALID_SETTINGS: 422,
    ErrorCode.INVALID_GEOMETRY: 422,
    ErrorCode.TOO_MANY_PIECES: 422,
    ErrorCode.PIECES_DO_NOT_FIT: 422,
    ErrorCode.EXPORT_FAILED: 500,
    ErrorCode.DXF_EXPORT_FAILED: 500,
    ErrorCode.SVG_EXPORT_FAILED: 500,
    ErrorCode.PDF_EXPORT_FAILED: 500,
    ErrorCode.JOB_NOT_FOUND: 404,
    ErrorCode.JOB_STATE_CONFLICT: 409,
    ErrorCode.FILE_NOT_READY: 404,
    ErrorCode.STORAGE_ERROR: 500,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.IMAGE_UNSUITABLE: 422,
    ErrorCode.INTERNAL_ERROR: 500,
}


class PuzzleError(Exception):
    def __init__(self, code: ErrorCode, message: str, detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def __str__(self) -> str:
        base = f"[{self.code}] {self.message}"
        return f"{base} ({self.detail})" if self.detail else base
