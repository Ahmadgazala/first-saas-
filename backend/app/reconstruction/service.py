"""Choose and run a reconstructor; turn its mesh into a canonical, sliceable body."""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
import trimesh

from app.core.config import get_settings
from app.reconstruction.base import Reconstructor
from app.reconstruction.triposr import TripoSRReconstructor

logger = logging.getLogger(__name__)

MIN_ALIGNMENT_IOU = 0.55
# A real object is rarely thinner than this fraction of its size; flat drawings and logos
# reconstruct as thin slabs, for which the estimated body is the better 3D shape.
MIN_DEPTH_RATIO = 0.14


def depth_ratio(mesh: trimesh.Trimesh) -> float:
    e = mesh.extents
    return float(e[2] / max(e[0], e[1]))


def get_reconstructor(settings=None) -> Reconstructor | None:
    s = settings or get_settings()
    return _reconstructor(s.reconstruction_backend, s.reconstruction_device, s.reconstruction_resolution,
                          s.reconstruction_allow_download)


@lru_cache
def _reconstructor(backend: str, device: str, resolution: int, allow_download: bool) -> Reconstructor | None:
    """One shared instance per configuration (the model is loaded lazily on first use)."""
    if backend == "none":
        return None
    if backend != "triposr":
        logger.warning("unknown reconstruction backend %r", backend)
        return None
    rec = TripoSRReconstructor(device=device, resolution=resolution, allow_download=allow_download)
    return rec if rec.available() else None


def mesh_to_bytes(mesh: trimesh.Trimesh) -> bytes:
    return mesh.export(file_type="ply")


def mesh_from_bytes(data: bytes) -> trimesh.Trimesh:
    import io

    return trimesh.load(io.BytesIO(data), file_type="ply", force="mesh", process=True)


def canonical_from_reconstruction(raw: trimesh.Trimesh, mask: np.ndarray, target_size_mm: float):
    """Repair, align to the photo's view and scale a reconstructed mesh.

    Returns (mesh, alignment IoU). The IoU between the model's projection and the photo's
    silhouette says how faithful the reconstruction is to what the user uploaded.
    """
    from app.geometry.volume import align_to_silhouette, prepare_mesh, remesh

    clean = remesh(raw)
    rotation, iou = align_to_silhouette(clean, mask)
    return prepare_mesh(clean, rotation, target_size_mm), iou
