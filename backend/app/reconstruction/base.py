from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import trimesh


class Reconstructor(ABC):
    """Turns one RGBA photo plus its object mask into a 3D mesh (any frame, any scale)."""

    name: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def reconstruct(self, rgba: np.ndarray, mask: np.ndarray) -> trimesh.Trimesh: ...
