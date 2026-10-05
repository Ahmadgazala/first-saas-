import os
from functools import lru_cache

# Default test runs are deterministic and offline: no neural segmentation, no AI 3D.
# The AI path has its own opt-in tests (tests/test_ai.py, marked "ai").
os.environ.setdefault("SEGMENTATION_MODEL", "none")
os.environ.setdefault("RECONSTRUCTION_BACKEND", "none")

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.geometry.engine import PuzzleGenerator
from app.geometry.normalize import normalize_silhouette
from app.geometry.types import Complexity, PuzzleParameters
from app.main import create_app
from app.services.task_queue import InlineTaskQueue
from tests.fixtures import ALL_MASKS


@lru_cache(maxsize=64)
def build_model(name: str, thickness: float = 3.0, pieces: int = 12, complexity: str = "medium", size: float = 250.0):
    """Cached puzzle model for a fixture silhouette (models are treated as read-only)."""
    params = PuzzleParameters(
        thickness_mm=thickness, piece_count=pieces, complexity=Complexity(complexity), target_size_mm=size
    )
    sil = normalize_silhouette(ALL_MASKS[name](), params.target_size_mm, params.min_feature_mm)
    return PuzzleGenerator(params).generate(sil.polygon)


@pytest.fixture
def client(tmp_path):
    """API client with inline job execution. Uses TEST_DATABASE_URL (e.g. PostgreSQL) if set."""
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    settings = Settings(database_url=url, storage_dir=tmp_path / "data", max_upload_mb=2,
                        reconstruction_backend="none", segmentation_model="none")
    app = create_app(settings, InlineTaskQueue())
    if url.startswith("postgresql"):
        from app.models.db import Base

        app.state.db.create_all()
        Base.metadata.drop_all(app.state.db.engine)
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


@lru_cache(maxsize=64)
def build_animal(name: str, thickness: float = 3.0, pieces: int = 20, complexity: str = "advanced",
                 size: float = 250.0):
    """Cached puzzle model for one of the animal/object silhouettes in tests/animals.py."""
    from tests.animals import ANIMALS

    params = PuzzleParameters(
        thickness_mm=thickness, piece_count=pieces, complexity=Complexity(complexity), target_size_mm=size
    )
    sil = normalize_silhouette(ANIMALS[name](), params.target_size_mm, params.min_feature_mm)
    return PuzzleGenerator(params).generate(sil.polygon)
