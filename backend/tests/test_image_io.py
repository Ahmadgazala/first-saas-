import io

import numpy as np
import pytest
from PIL import Image

from app.core.errors import ErrorCode, PuzzleError
from app.services.storage import LocalStorageService, validate_key
from app.validators.file_validators import sanitize_filename, sniff_image_type, validate_upload_size
from app.vision.image_io import load_image
from tests.fixtures import animal_mask, encode, mask_to_photo, transparent_png


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_loads_supported_formats(fmt):
    data = encode(mask_to_photo(animal_mask()), fmt)
    img = load_image(data)
    assert img.format == fmt
    assert img.rgba.shape == (512, 512, 4)
    assert img.rgba.dtype == np.uint8


def test_detects_alpha_channel():
    img = load_image(transparent_png(animal_mask()))
    assert img.has_alpha
    assert (img.rgba[..., 3] == 0).any()


def test_rejects_non_image_bytes():
    with pytest.raises(PuzzleError) as e:
        load_image(b"#!/bin/sh\necho pwned\n" * 20)
    assert e.value.code == ErrorCode.UNSUPPORTED_FORMAT


def test_rejects_truncated_image():
    data = encode(mask_to_photo(animal_mask()), "PNG")
    with pytest.raises(PuzzleError) as e:
        load_image(data[: len(data) // 3])
    assert e.value.code == ErrorCode.INVALID_FILE


def test_rejects_gif_even_with_png_extension():
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, format="GIF")
    with pytest.raises(PuzzleError) as e:
        sniff_image_type(buf.getvalue())
    assert e.value.code == ErrorCode.UNSUPPORTED_FORMAT


def test_resolution_limits():
    with pytest.raises(PuzzleError) as e:
        load_image(encode(np.zeros((32, 32, 3), np.uint8)))
    assert e.value.code == ErrorCode.INVALID_RESOLUTION
    with pytest.raises(PuzzleError) as e:
        load_image(encode(np.zeros((64, 9000, 3), np.uint8)))
    assert e.value.code == ErrorCode.INVALID_RESOLUTION


def test_large_image_warns_and_loads():
    img = load_image(encode(np.zeros((80, 4200, 3), np.uint8)), recommended_side=4096)
    assert img.width == 4200 and img.warnings


def test_exif_orientation_is_applied():
    arr = np.zeros((100, 200, 3), np.uint8)
    pil = Image.fromarray(arr)
    exif = pil.getexif()
    exif[0x0112] = 6  # rotate 90° on display
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", exif=exif)
    img = load_image(buf.getvalue())
    assert (img.width, img.height) == (100, 200)


def test_upload_size_validation():
    validate_upload_size(10, 100)
    with pytest.raises(PuzzleError) as e:
        validate_upload_size(101, 100)
    assert e.value.code == ErrorCode.FILE_TOO_LARGE
    with pytest.raises(PuzzleError):
        validate_upload_size(0, 100)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\lion.png", "lion.png"),
        ("my lion<script>.jpg", "my lion_script_.jpg"),
        ("", "image"),
        ("....", "image"),
        ("ünïcødé.webp", "unicde.webp"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_length():
    name = sanitize_filename("a" * 300 + ".png")
    assert len(name) <= 80 and name.endswith(".png")


@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../b", "a//b", "a\\b", "", "a/./b", "a/b c"])
def test_storage_rejects_unsafe_keys(key, tmp_path):
    storage = LocalStorageService(tmp_path)
    with pytest.raises(PuzzleError):
        storage.save_bytes(key, b"x")


def test_storage_roundtrip(tmp_path):
    storage = LocalStorageService(tmp_path)
    storage.save_bytes("generated/abc/file.svg", b"<svg/>")
    assert storage.read_bytes("generated/abc/file.svg") == b"<svg/>"
    assert storage.local_path("generated/abc/file.svg").is_relative_to(tmp_path.resolve())
    storage.delete_prefix("generated/abc")
    assert not storage.exists("generated/abc/file.svg")
    assert validate_key("uploads/1/original.png")
