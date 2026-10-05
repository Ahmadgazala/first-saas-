import cv2
import numpy as np
import pytest

from app.core.errors import PuzzleError
from app.services.pipeline import extract_silhouette
from app.vision.image_io import load_image
from app.vision.segmentation import clean_mask, fill_holes, mask_iou, segment, select_main_component
from tests.fixtures import animal_mask, circle_mask, encode, mask_to_photo, transparent_png


def _resized(mask, shape):
    return cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)


def test_alpha_channel_is_authoritative():
    mask = animal_mask()
    res = segment(load_image(transparent_png(mask)).rgba)
    assert res.method == "alpha"
    assert res.confidence >= 0.95
    assert mask_iou(res.mask, _resized(mask, res.mask.shape)) > 0.97


@pytest.mark.parametrize("make", [animal_mask, circle_mask])
def test_plain_background_photo(make):
    mask = make()
    res = segment(load_image(encode(mask_to_photo(mask), "JPEG")).rgba)
    assert res.method in ("background", "grabcut", "threshold")
    assert mask_iou(res.mask, _resized(mask, res.mask.shape)) > 0.93
    assert res.confidence > 0.6
    assert not any("confidence is low" in w for w in res.warnings)


def test_dark_object_on_gradient_background():
    mask = animal_mask()
    photo = mask_to_photo(mask, background=(90, 140, 220), foreground=(30, 30, 30))
    res = segment(load_image(encode(photo)).rgba)
    assert mask_iou(res.mask, _resized(mask, res.mask.shape)) > 0.93


def test_output_is_binary_white_object_black_background():
    res = segment(load_image(encode(mask_to_photo(circle_mask()))).rgba)
    assert set(np.unique(res.mask)) <= {0, 255}
    assert res.mask[res.mask.shape[0] // 2, res.mask.shape[1] // 2] == 255
    assert res.mask[0, 0] == 0


def test_featureless_image_is_rejected():
    flat = np.full((200, 200, 3), 128, np.uint8)
    with pytest.raises(PuzzleError) as e:
        extract_silhouette(encode(flat))
    assert e.value.code == "OBJECT_NOT_DETECTED"


def test_noise_and_small_components_are_removed():
    mask = circle_mask().copy()
    rng = np.random.default_rng(0)
    for _ in range(40):
        x, y = rng.integers(0, 512, 2)
        cv2.circle(mask, (int(x), int(y)), 2, 255, -1)  # speckles
    cv2.circle(mask, (480, 30), 12, 255, -1)  # small separate blob
    cleaned = clean_mask(mask)
    n, _ = cv2.connectedComponents((cleaned > 0).astype(np.uint8))
    assert n == 2  # background + one object
    assert mask_iou(cleaned, circle_mask()) > 0.97


def test_fill_holes_and_main_component():
    mask = circle_mask().copy()
    cv2.circle(mask, (256, 256), 5, 0, -1)
    assert fill_holes(mask, 0.02)[256, 256] == 255
    two = np.zeros((100, 100), np.uint8)
    two[10:20, 10:20] = 255
    two[40:90, 40:90] = 255
    main = select_main_component(two)
    assert main[60, 60] == 255 and main[15, 15] == 0


def test_explicit_method_override():
    data = encode(mask_to_photo(animal_mask()))
    sil = extract_silhouette(data, "threshold")
    assert sil.method == "threshold"
    sil = extract_silhouette(data, "alpha")  # no alpha: falls back with a warning
    assert any("transparency" in w for w in sil.warnings)
