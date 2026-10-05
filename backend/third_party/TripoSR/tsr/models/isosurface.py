from typing import Callable, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn


def marching_cubes(vol: torch.Tensor, thresh: float):
    """CPU replacement for torchmcubes.marching_cubes (PuzzleCut AI patch).

    Uses scikit-image and returns vertices in torchmcubes' (x, y, z) order for a volume
    indexed [z][y][x], so the rest of TripoSR is unchanged.
    """
    from skimage.measure import marching_cubes as _mc

    arr = vol.detach().cpu().float().numpy()
    if not (arr.min() < thresh < arr.max()):
        return torch.zeros((0, 3)), torch.zeros((0, 3), dtype=torch.long)
    verts, faces, _, _ = _mc(arr, level=thresh)
    verts = np.ascontiguousarray(verts[:, ::-1])
    return torch.from_numpy(verts).float(), torch.from_numpy(faces.astype(np.int64))


class IsosurfaceHelper(nn.Module):
    points_range: Tuple[float, float] = (0, 1)

    @property
    def grid_vertices(self) -> torch.FloatTensor:
        raise NotImplementedError


class MarchingCubeHelper(IsosurfaceHelper):
    def __init__(self, resolution: int) -> None:
        super().__init__()
        self.resolution = resolution
        self.mc_func: Callable = marching_cubes
        self._grid_vertices: Optional[torch.FloatTensor] = None

    @property
    def grid_vertices(self) -> torch.FloatTensor:
        if self._grid_vertices is None:
            # keep the vertices on CPU so that we can support very large resolution
            x, y, z = (
                torch.linspace(*self.points_range, self.resolution),
                torch.linspace(*self.points_range, self.resolution),
                torch.linspace(*self.points_range, self.resolution),
            )
            x, y, z = torch.meshgrid(x, y, z, indexing="ij")
            verts = torch.cat(
                [x.reshape(-1, 1), y.reshape(-1, 1), z.reshape(-1, 1)], dim=-1
            ).reshape(-1, 3)
            self._grid_vertices = verts
        return self._grid_vertices

    def forward(
        self,
        level: torch.FloatTensor,
    ) -> Tuple[torch.FloatTensor, torch.LongTensor]:
        level = -level.view(self.resolution, self.resolution, self.resolution)
        try:
            v_pos, t_pos_idx = self.mc_func(level.detach(), 0.0)
        except AttributeError:
            print("torchmcubes was not compiled with CUDA support, use CPU version instead.")
            v_pos, t_pos_idx = self.mc_func(level.detach().cpu(), 0.0)
        v_pos = v_pos[..., [2, 1, 0]]
        v_pos = v_pos / (self.resolution - 1.0)
        return v_pos.to(level.device), t_pos_idx.to(level.device)
