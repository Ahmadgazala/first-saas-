"""Procedural 3D test models (no external files)."""

from __future__ import annotations

import numpy as np
import trimesh


def _ellipsoid(center, radii, subdivisions=3):
    m = trimesh.creation.icosphere(subdivisions=subdivisions, radius=1.0)
    m.apply_scale(radii)
    m.apply_translation(center)
    return m


def _limb(start, end, radius):
    start, end = np.asarray(start, float), np.asarray(end, float)
    return trimesh.creation.cylinder(radius=radius, segment=[start, end], sections=24)


def quadruped() -> trimesh.Trimesh:
    """A horse-like animal in the canonical frame: x along the body, y up, z = width.
    Four legs at the four corners (left legs at +z, right legs at -z), a neck, a head with
    a muzzle, ears and a tail."""
    parts = [
        _ellipsoid((0, 110, 0), (95, 40, 32)),  # barrel
        _ellipsoid((70, 115, 0), (40, 42, 34)),  # chest
        _ellipsoid((-70, 115, 0), (40, 40, 33)),  # hindquarters
        _limb((95, 125, 0), (135, 200, 0), 16),  # neck
        _ellipsoid((150, 205, 0), (32, 18, 15)),  # head
        _ellipsoid((178, 195, 0), (16, 12, 11)),  # muzzle
        _limb((140, 215, 6), (138, 238, 7), 4),  # ears
        _limb((140, 215, -6), (138, 238, -7), 4),
        _limb((-100, 125, 0), (-125, 60, 0), 7),  # tail
    ]
    for x in (70, -70):
        for z in (20, -20):
            parts.append(_limb((x, 100, z), (x + (5 if x > 0 else -5), 0, z), 9))
    return trimesh.util.concatenate(parts)


def biped() -> trimesh.Trimesh:
    """Biped theropod: big head with a jaw, short arms, two legs side by side, long tail."""
    parts = [
        _ellipsoid((0, 130, 0), (60, 38, 30)),  # torso
        _limb((45, 145, 0), (85, 200, 0), 14),  # neck
        _ellipsoid((110, 215, 0), (42, 20, 16)),  # skull
        _ellipsoid((112, 192, 0), (36, 8, 12)),  # lower jaw
        _limb((40, 115, 18), (55, 95, 22), 4), _limb((40, 115, -18), (55, 95, -22), 4),  # arms
        _limb((-50, 135, 0), (-200, 120, 0), 12),  # tail root
        _limb((-200, 120, 0), (-280, 105, 0), 6),  # tail tip
    ]
    for z in (22, -22):
        parts += [_ellipsoid((-10, 105, z), (22, 32, 12)),  # thigh
                  _limb((-5, 80, z), (5, 12, z), 8),  # shin
                  _ellipsoid((12, 6, z), (22, 6, 9))]  # foot
    return trimesh.util.concatenate(parts)


def bird() -> trimesh.Trimesh:
    """Perched bird: upright body, head with a beak, folded wings, tail, two legs."""
    parts = [
        _ellipsoid((0, 120, 0), (34, 55, 30)),  # body
        _ellipsoid((14, 190, 0), (24, 24, 22)),  # head
        _limb((34, 192, 0), (56, 186, 0), 5),  # beak
        _ellipsoid((-6, 115, 26), (26, 48, 6)), _ellipsoid((-6, 115, -26), (26, 48, 6)),  # wings
        _ellipsoid((-30, 55, 0), (16, 40, 8)),  # tail
    ]
    for z in (10, -10):
        parts += [_limb((4, 72, z), (6, 18, z), 3.5), _ellipsoid((12, 15, z), (12, 3, 5))]
    return trimesh.util.concatenate(parts)
