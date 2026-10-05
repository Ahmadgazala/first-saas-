"""TripoSR (MIT, Tripo AI & Stability AI) running locally on CPU or Apple GPU (MPS).

The vendored source lives in backend/third_party/TripoSR. Weights (~1.7 GB) are fetched
from Hugging Face on first use and cached. One model instance is shared by all jobs and
reconstructions run one at a time to keep memory bounded.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import trimesh

from app.core.errors import ErrorCode, PuzzleError
from app.reconstruction.base import Reconstructor

logger = logging.getLogger(__name__)

TSR_ROOT = Path(__file__).resolve().parents[2] / "third_party" / "TripoSR"
REPO_ID = "stabilityai/TripoSR"


class TripoSRReconstructor(Reconstructor):
    name = "triposr"

    def __init__(self, device: str = "auto", resolution: int = 256, allow_download: bool = True):
        self.device_pref = device
        self.resolution = resolution
        self.allow_download = allow_download
        self._model = None
        self._device = "cpu"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ availability

    def available(self) -> bool:
        try:
            import torch  # noqa: F401
            from huggingface_hub import try_to_load_from_cache
        except ImportError:
            return False
        if not (TSR_ROOT / "tsr" / "system.py").is_file():
            return False
        if self.allow_download:
            return True
        cached = try_to_load_from_cache(REPO_ID, "model.ckpt")
        return isinstance(cached, str)

    def _load(self):
        if self._model is not None:
            return self._model
        import torch

        if str(TSR_ROOT) not in sys.path:
            sys.path.insert(0, str(TSR_ROOT))
        from tsr.system import TSR

        t0 = time.perf_counter()
        model = TSR.from_pretrained(REPO_ID, config_name="config.yaml", weight_name="model.ckpt")
        model.renderer.set_chunk_size(8192)
        device = self.device_pref
        if device == "auto":
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        model.to(device)
        self._model, self._device = model, device
        logger.info("TripoSR loaded on %s in %.1fs", device, time.perf_counter() - t0)
        return model

    # ------------------------------------------------------------------ inference

    def reconstruct(self, rgba: np.ndarray, mask: np.ndarray) -> trimesh.Trimesh:
        import torch

        image = _prepare_input(rgba, mask)
        with self._lock:
            try:
                model = self._load()
                t0 = time.perf_counter()
                with torch.no_grad():
                    codes = model([image], device=self._device)
                    meshes = model.extract_mesh(codes, False, resolution=self.resolution)
            except PuzzleError:
                raise
            except Exception as exc:
                if self._device != "cpu":
                    logger.warning("TripoSR failed on %s, retrying on CPU", self._device, exc_info=True)
                    self._model.to("cpu")
                    self._device = "cpu"
                    with torch.no_grad():
                        codes = self._model([image], device="cpu")
                        meshes = self._model.extract_mesh(codes, False, resolution=self.resolution)
                else:
                    raise PuzzleError(ErrorCode.INTERNAL_ERROR, "3D reconstruction failed.", repr(exc)) from exc
            logger.info("TripoSR reconstruction took %.1fs", time.perf_counter() - t0)
        mesh = meshes[0]
        if len(mesh.faces) == 0:
            raise PuzzleError(ErrorCode.OBJECT_NOT_DETECTED, "3D reconstruction produced an empty model.")
        return trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.faces), process=True)


def _prepare_input(rgba: np.ndarray, mask: np.ndarray, ratio: float = 0.85, size: int = 512):
    """Object cut out by our mask, centred on grey, filling `ratio` of a square (TripoSR's
    expected input)."""
    from PIL import Image

    h, w = rgba.shape[:2]
    m = cv2.resize((mask > 0).astype(np.uint8), (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    m = cv2.GaussianBlur(m, (0, 0), 1.0)[..., None]
    rgb = rgba[..., :3].astype(np.float32) / 255.0
    comp = rgb * m + 0.5 * (1.0 - m)
    ys, xs = np.nonzero(m[..., 0] > 0.5)
    if ys.size == 0:
        raise PuzzleError(ErrorCode.OBJECT_NOT_DETECTED, "Unable to detect the object.")
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = comp[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    side = int(max(ch, cw) / ratio)
    canvas = np.full((side, side, 3), 0.5, np.float32)
    oy, ox = (side - ch) // 2, (side - cw) // 2
    canvas[oy: oy + ch, ox: ox + cw] = crop
    canvas = cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)
    return Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8))
