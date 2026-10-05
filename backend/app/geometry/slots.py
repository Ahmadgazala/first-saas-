"""Slot sizing and kerf compensation.

Slot sizing
-----------
Two ribs of thickness T interlock through half-lap ("egg-crate") slots. Each slot is cut
to the *nominal* width

    W_slot = T + clearance

so a T-thick rib slides in with `clearance` of total play. The two slots of a joint meet
at the midpoint of the ribs' common overlap so both pieces end flush.

Kerf compensation
-----------------
The laser removes a strip of width `kerf` centred on the tool path. If the tool path ran
along the nominal outline, every outer edge would move inward by kerf/2 (parts shrink) and
every slot wall would move outward by kerf/2 (slots grow by a full kerf, joints get loose).

We therefore export the tool path as the nominal outline offset OUTWARD from the material
by kerf/2 (Shapely buffer, mitred corners):

    outer contours  grow  by kerf/2 per side  -> cut part is exactly nominal size
    slot walls      move inward by kerf/2      -> toolpath slot width = W_slot - kerf
    holes           shrink by kerf/2 per side  -> cut hole is exactly nominal size

After cutting, the physical slot width is W_slot = T + clearance and the physical outline
equals the design outline. Engraving (labels) is never compensated: it is not a cut.
"""

from __future__ import annotations

from shapely.geometry import Polygon, box

from app.core.errors import ErrorCode, PuzzleError

BIG = 1.0e5


def nominal_slot_width(thickness_mm: float, clearance_mm: float) -> float:
    if thickness_mm <= 0:
        raise PuzzleError(ErrorCode.INVALID_SETTINGS, "Material thickness must be positive.")
    if clearance_mm < 0:
        raise PuzzleError(ErrorCode.INVALID_SETTINGS, "Clearance cannot be negative.")
    return thickness_mm + clearance_mm


def toolpath_slot_width(thickness_mm: float, clearance_mm: float, kerf_mm: float) -> float:
    """Width of the slot as drawn in the exported (kerf-compensated) file."""
    width = nominal_slot_width(thickness_mm, clearance_mm) - kerf_mm
    if width <= 0:
        raise PuzzleError(ErrorCode.INVALID_SETTINGS, "Kerf is larger than the slot width.")
    return width


def slot_split(y_low: float, y_high: float) -> float:
    """Height at which the two half-lap slots of a joint meet."""
    if y_high <= y_low:
        raise ValueError("empty overlap")
    return 0.5 * (y_low + y_high)


def slot_cutter(center: float, width: float, split: float, opens_up: bool) -> Polygon:
    """Rectangle removed from a piece: from `split` through the piece edge (up or down)."""
    half = width / 2.0
    if opens_up:
        return box(center - half, split, center + half, BIG)
    return box(center - half, -BIG, center + half, split)


def compensate_kerf(polygon: Polygon, kerf_mm: float) -> Polygon:
    """Offset a nominal piece outline by kerf/2 away from the material (see module docs)."""
    if kerf_mm < 0:
        raise PuzzleError(ErrorCode.INVALID_SETTINGS, "Kerf cannot be negative.")
    if kerf_mm == 0:
        return polygon
    out = polygon.buffer(kerf_mm / 2.0, join_style="mitre", mitre_limit=4.0)
    if not isinstance(out, Polygon) or not out.is_valid:
        raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "Generated geometry is invalid.", "kerf offset failed")
    return out
