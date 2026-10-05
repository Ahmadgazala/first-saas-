"""Opt-in tests that run the real AI models (run with: pytest -m ai).

They need the rembg IS-Net weights and the TripoSR weights in the local caches and take
roughly a minute on an Apple M-series CPU/GPU.
"""

from pathlib import Path

import numpy as np
import pytest

from app.core.config import Settings
from app.geometry.engine import PuzzleGenerator
from app.geometry.types import Complexity, PuzzleParameters
from app.validators.geometry_validator import validate_model

pytestmark = pytest.mark.ai
SAMPLE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "samples" / "horse-silhouette.jpg"


def _reconstructor():
    from app.reconstruction.service import get_reconstructor

    rec = get_reconstructor(Settings(reconstruction_backend="triposr", reconstruction_allow_download=False))
    if rec is None:
        pytest.skip("TripoSR weights are not installed")
    return rec


def test_ai_background_removal(monkeypatch):
    monkeypatch.setenv("SEGMENTATION_MODEL", "isnet-general-use")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.vision.image_io import load_image
    from app.vision.segmentation import segment
    from tests.animals import horse
    from tests.fixtures import encode, mask_to_photo

    res = segment(load_image(encode(mask_to_photo(horse()))).rgba)
    get_settings.cache_clear()
    if not any(c["method"] == "ai" for c in res.candidates):
        pytest.skip("rembg model not available")
    assert res.confidence > 0.6


def test_photo_to_real_3d_puzzle():
    from app.reconstruction.service import canonical_from_reconstruction, depth_ratio
    from app.vision.image_io import load_image, resize_max_side
    from app.vision.segmentation import segment

    rec = _reconstructor()
    img = load_image(SAMPLE.read_bytes())
    mask = segment(img.rgba).mask
    raw = rec.reconstruct(resize_max_side(img.rgba, 768), mask)
    mesh, iou = canonical_from_reconstruction(raw, mask, 250.0)
    assert iou > 0.6
    assert depth_ratio(mesh) > 0
    model = PuzzleGenerator(PuzzleParameters(piece_count=12, complexity=Complexity.MEDIUM,
                                             orientation="vertical")).generate_from_mesh(mesh)
    assert validate_model(model).ok
    assert np.isfinite(model.object_size_mm).all()
