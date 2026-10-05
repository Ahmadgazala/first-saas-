"""1D section queries on 2D pieces: where along a line or strip does a piece have material."""

from __future__ import annotations

from shapely.geometry import LineString, Polygon, box

from app.geometry.slots import BIG

Interval = tuple[float, float]


def _merge(intervals: list[Interval], gap: float = 1e-6) -> list[Interval]:
    intervals = sorted(i for i in intervals if i[1] > i[0])
    out: list[Interval] = []
    for lo, hi in intervals:
        if out and lo <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def line_intervals(poly: Polygon, u: float) -> list[Interval]:
    """Intervals of v where the vertical line u = const lies inside the polygon."""
    minx, miny, maxx, maxy = poly.bounds
    if not (minx < u < maxx):
        return []
    inter = poly.intersection(LineString([(u, miny - 1.0), (u, maxy + 1.0)]))
    parts = getattr(inter, "geoms", [inter])
    spans = []
    for g in parts:
        if g.is_empty or g.geom_type not in ("LineString", "LinearRing"):
            continue
        ys = [c[1] for c in g.coords]
        spans.append((min(ys), max(ys)))
    return _merge(spans)


def strip_intervals(poly: Polygon, u_lo: float, u_hi: float) -> list[Interval]:
    """v-extents of the connected parts of the polygon inside the strip u_lo <= u <= u_hi.

    This is the conservative footprint of a slab occupying that strip: any v where the
    piece has material anywhere in the strip is covered.
    """
    inter = poly.intersection(box(u_lo, -BIG, u_hi, BIG))
    parts = getattr(inter, "geoms", [inter])
    spans = []
    for g in parts:
        if g.is_empty or g.area <= 1e-9:
            continue
        _, lo, _, hi = g.bounds
        spans.append((lo, hi))
    return _merge(spans)


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def core_intervals(poly: Polygon, u: float, half_width: float) -> list[Interval]:
    """v where the polygon contains material across the whole strip u ± half_width (sampled)."""
    result = line_intervals(poly, u)
    for k in (-1.0, -0.5, 0.5, 1.0):
        if not result:
            break
        result = intersect(result, line_intervals(poly, u + k * half_width))
    return result


def measure(intervals: list[Interval], lo: float = -BIG, hi: float = BIG) -> float:
    return sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in intervals)
