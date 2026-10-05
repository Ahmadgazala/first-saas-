"""ImageSuitabilityAnalyzer: good side views pass, unsuitable images are rejected with guidance."""

import cv2
import numpy as np
import pytest

from app.vision.segmentation import segment
from app.vision.suitability import GUIDANCE, ImageSuitabilityAnalyzer, structure_overlay
from tests.animals import ANIMALS
from tests.fixtures import mask_to_photo


def rgba(rgb: np.ndarray) -> np.ndarray:
    return np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])


def analyze(rgb: np.ndarray):
    img = rgba(rgb)
    seg = segment(img, "auto")
    return ImageSuitabilityAnalyzer().analyze(img, seg.mask, seg.confidence)


@pytest.mark.parametrize("name", ["horse", "trex", "eagle", "fish", "deer"])
def test_clear_side_views_are_suitable(name):
    r = analyze(mask_to_photo(ANIMALS[name]()))
    assert r.suitable and r.overall_confidence >= 0.7
    assert r.recommended_view == "side" and r.guidance is None
    d = r.to_dict()
    assert d["confidence_kind"] == "estimated"
    assert set(d) >= {"suitable", "overall_confidence", "silhouette_confidence", "structure_confidence",
                      "recommended_view", "warnings"}
    assert all(0.0 <= v <= 1.0 for v in d["scores"].values())


def test_structure_is_reported_for_animals():
    r = analyze(mask_to_photo(ANIMALS["horse"]()))
    assert r.structure["has_head"] and r.structure["limb_count"] >= 3


def test_multiple_subjects_are_rejected():
    two = np.zeros((768, 1024), np.uint8)
    two[:, :512] = cv2.resize(ANIMALS["horse"](), (512, 768))
    two[:, 512:] = cv2.resize(ANIMALS["wolf"](), (512, 768))
    r = analyze(mask_to_photo(two))
    assert not r.suitable
    assert any("more than one" in b for b in r.blocking)
    assert r.guidance == GUIDANCE


def test_empty_image_is_rejected_quickly():
    r = analyze(np.full((600, 800, 3), 200, np.uint8))
    assert not r.suitable and r.blocking


def test_poor_background_lowers_confidence():
    rng = np.random.default_rng(3)
    clean = mask_to_photo(ANIMALS["horse"]())
    busy = np.clip(clean.astype(int) + rng.normal(0, 90, clean.shape), 0, 255).astype(np.uint8)
    assert analyze(busy).overall_confidence < analyze(clean).overall_confidence


def test_subject_cut_off_by_the_frame_is_flagged():
    m = ANIMALS["horse"]()
    cropped = m[:, 300:]  # legs and tail cut by the left border
    r = analyze(mask_to_photo(np.ascontiguousarray(cropped)))
    assert r.scores["visibility_score"] < 1.0
    assert any("border" in w for w in r.warnings)


def test_thin_limbs_still_analyzed():
    r = analyze(mask_to_photo(ANIMALS["thin_legs"]()))
    assert r.structure is not None


def test_structure_overlay_png():
    import io

    from PIL import Image

    from app.vision.image_io import to_png_bytes

    photo = mask_to_photo(ANIMALS["horse"]())
    png = structure_overlay(to_png_bytes(rgba(photo)), to_png_bytes(ANIMALS["horse"]()))
    img = Image.open(io.BytesIO(png))
    assert img.size == (1024, 768)
