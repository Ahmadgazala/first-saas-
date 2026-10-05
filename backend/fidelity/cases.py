"""Test cases for the fidelity harness.

fixture  drawn side-view masks from tests/animals.py: exact 2D ground truth (the mask), no 3D
         truth. The "photo" fed to the pipeline is tests.fixtures.mask_to_photo(mask).
gtmesh   procedural 3D meshes: exact 3D ground truth. Two inputs per mesh:
           upload  the mesh itself (production 3D-model upload path)
           photo   its orthographic side-view mask rendered as a photo (silhouette path)
photo    real photographs (no ground-truth mask: the reference is the pipeline's own
         segmentation, so segmentation error itself is NOT measurable for these).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

BACKEND = Path(__file__).resolve().parents[1]
UPLOADS = BACKEND / "data" / "uploads"
# Local-only real photos (owl.jpg, parrot.jpg, penguin.jpg, ...): third-party images, NOT in the repository
# (backend/fidelity/photos/ is git-ignored). Point PUZZLECUT_FIDELITY_PHOTOS at another folder to override.
SCRATCH_PHOTOS = Path(os.environ.get("PUZZLECUT_FIDELITY_PHOTOS", BACKEND / "fidelity" / "photos"))


@dataclass
class Case:
    name: str
    kind: str  # fixture | gtmesh | photo
    image: bytes | None = None
    gt_mask: np.ndarray | None = None
    gt_mesh: trimesh.Trimesh | None = None
    cached_reconstruction: Path | None = None
    note: str = ""
    full_body: bool = True
    tags: list[str] = field(default_factory=list)


def dino_mesh() -> trimesh.Trimesh:
    """Biped theropod (procedural GT; defined in tests/meshes.py)."""
    from tests.meshes import biped

    return biped()


def bird_mesh() -> trimesh.Trimesh:
    """Perched bird (procedural GT; defined in tests/meshes.py)."""
    from tests.meshes import bird

    return bird()


def fixtures() -> list[Case]:
    from tests.animals import ANIMALS

    return [Case(name, "fixture", gt_mask=fn(), note="drawn side-view mask") for name, fn in ANIMALS.items()]


def gt_meshes() -> list[Case]:
    from tests.meshes import quadruped

    return [
        Case("gt_quadruped", "gtmesh", gt_mesh=quadruped(), note="tests/meshes.quadruped (procedural GT)"),
        Case("gt_dino", "gtmesh", gt_mesh=dino_mesh(), note="harness procedural biped (GT)"),
        Case("gt_bird", "gtmesh", gt_mesh=bird_mesh(), note="harness procedural perched bird (GT)"),
    ]


# Distinct real photos: (name, upload id or scratch file, full body?, note)
REAL = [
    ("photo_trex_kit", "136a26f9-79bf-43d4-8446-2720eae745e3", True, "the user's reference T-Rex kit photo"),
    ("photo_lion_walk", "32610541-aa00-43c7-835e-23b5220add22", True, "lion walking, side view"),
    ("photo_lion_grass", "3b875198-b9e8-47f8-9db1-36dee6cdbc13", True, "lion lying on grass, busy background"),
    ("photo_lion_bw", "3df63630-434d-4f86-8c7a-4e1f38bbeaaf", True, "lion lying, black background"),
    ("photo_cat_snow", "7b9e93d8-3351-4cd7-a6d7-2b90b28729a9", True, "cat walking on snow, 3/4 view"),
    ("photo_zebra_head", "3f67e443-b9ac-46ea-bc2c-5c00d90f8958", False, "zebra head/neck crop"),
    ("photo_eagle_head", "79c236fa-fd2a-420e-8c6a-2755f8cd21ba", False, "eagle head crop"),
    ("photo_owl_face", "owl.jpg", False, "owl face, frontal"),
    ("photo_parrot_head", "parrot.jpg", False, "parrot head crop"),
    ("photo_penguin", "penguin.jpg", False, "penguin upper body, cut off"),
]


def _image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def photos() -> list[Case]:
    out = []
    by_hash: dict[str, Path] = {}
    for d in sorted(UPLOADS.glob("*")):
        rec = d / "reconstruction.ply"
        orig = next(iter(sorted(d.glob("original.*"))), None)
        if rec.exists() and orig is not None:
            by_hash.setdefault(_image_hash(orig.read_bytes()), rec)
    for name, src, full, note in REAL:
        if src.endswith(".jpg"):
            path = SCRATCH_PHOTOS / src
        else:
            path = next(iter(sorted((UPLOADS / src).glob("original.*"))), None)
        if path is None or not path.exists():
            continue
        data = path.read_bytes()
        out.append(Case(name, "photo", image=data, cached_reconstruction=by_hash.get(_image_hash(data)),
                        note=note, full_body=full))
    return out


def all_cases() -> list[Case]:
    return fixtures() + gt_meshes() + photos()
