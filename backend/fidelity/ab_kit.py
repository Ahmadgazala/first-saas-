"""A/B: fossil kit generated from the mesh (sections) vs from its outline and depth only.

Same prepared mesh for both (production upload path: remesh -> prepare_mesh), same settings
(web-app defaults); 3D metrics against that mesh at `--res` mm voxels.

    .venv/bin/python -m fidelity.ab_kit [--res 1.0]

Prints a Markdown table "mesh-driven / depth-only" per body (used in PUZZLECUT_CONTEXT.md §9).
"""

from __future__ import annotations

import argparse
import logging
import warnings

import numpy as np
import trimesh

warnings.filterwarnings("ignore")
logging.getLogger("app").setLevel(logging.ERROR)

KEYS = ("proj_iou_front", "proj_iou_top", "volume_iou", "coverage_6mm", "material_outside_ref", "chamfer_mm")


def bodies() -> dict:
    from tests.meshes import biped, bird, quadruped

    def slim():
        m = quadruped()
        m.apply_scale([1.0, 1.0, 0.6])
        return m

    def bulge():
        lump = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        lump.apply_scale((60, 28, 30))
        lump.apply_translation((0, 105, 28))
        return trimesh.util.concatenate([quadruped(), lump])

    return {"quadruped": quadruped, "slim quadruped": slim, "biped": biped, "bird": bird, "off-centre bulge": bulge}


def compare(mesh: trimesh.Trimesh, res: float = 1.0) -> dict:
    from app.geometry.kit.builder import KitGenerator
    from app.geometry.types import Style
    from app.geometry.volume import MeshVolume, prepare_mesh, remesh
    from fidelity import assembly as A
    from fidelity.run import DEFAULTS, params_for, voxel_block

    body = prepare_mesh(remesh(mesh), np.eye(3), DEFAULTS["target_size_mm"])
    v = MeshVolume(body)
    out = {}
    for name, volume in (("mesh", v), ("depth_only", None)):
        model = KitGenerator(params_for(Style.KIT, "ab")).generate(v.silhouette, depth=v.depth, volume=volume)
        out[name] = voxel_block(body, A.model_slabs(model), None, res, "mesh")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", type=float, default=1.0)
    a = ap.parse_args()
    print("| body | " + " | ".join(KEYS) + " |")
    print("|---" * (len(KEYS) + 1) + "|")
    for name, fn in bodies().items():
        r = compare(fn(), a.res)
        cells = [f"{r['mesh'][k]:.3f} / {r['depth_only'][k]:.3f}" for k in KEYS]
        print(f"| {name} | " + " | ".join(cells) + " |", flush=True)


if __name__ == "__main__":
    main()
