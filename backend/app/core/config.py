"""Application configuration loaded from environment variables (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/puzzlecut"
    storage_dir: Path = Path(__file__).resolve().parents[2] / "data"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    log_level: str = "INFO"

    max_upload_mb: int = 15
    max_image_side_px: int = 8192
    recommended_image_side_px: int = 4096
    min_image_side_px: int = 64

    # Number of worker threads used by the in-process job queue.
    worker_threads: int = 2

    # Photo -> 3D reconstruction ("triposr" or "none"), device ("auto" = Apple GPU / CPU),
    # marching-cubes resolution and whether model weights may be downloaded on first use.
    reconstruction_backend: str = "triposr"
    reconstruction_device: str = "auto"
    reconstruction_resolution: int = 256
    reconstruction_allow_download: bool = True

    # AI background removal model (rembg) used for photos; "none" disables it.
    segmentation_model: str = "isnet-general-use"

    # 3D model uploads (STL / OBJ / GLB / PLY) may be larger than photos.
    max_model_upload_mb: int = 50

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
