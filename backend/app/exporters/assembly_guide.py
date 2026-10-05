"""Assembly guide PDF (A4), built from the generated geometry only.

Sections: overview (assembled render, specification, quality), parts overview (the real
cut sheet with IDs), piece identification (2D shape, locator render, connected pieces for
every piece), exploded view with the slot table, and the assembly sequence with an
illustration of every intermediate stage.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.errors import ErrorCode, PuzzleError
from app.exporters.metadata import connections
from app.exporters.pdf import MM, draw_sheet
from app.exporters.render import (
    GHOST_COLOR,
    HIGHLIGHT_COLOR,
    Camera,
    RenderItem,
    exploded_items,
    insertion_vector,
    render,
)
from app.exporters.sheet import SheetGeometry
from app.geometry.nesting import Layout
from app.geometry.types import Piece, PieceKind, PieceRole, PuzzleModel

PAGE_W, PAGE_H = A4
MARGIN = 16 * MM
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
ACCENT = colors.HexColor("#2563EB")
RULE = colors.HexColor("#E5E7EB")
ROLE_NAMES = {
    PieceRole.SPINE: "Backbone (spine)",
    PieceRole.PROFILE: "Side profile",
    PieceRole.DETAIL: "Detail plate",
    PieceRole.CROSS: "Cross rib",
    PieceRole.RIB: "Arch rib",
    PieceRole.VERTEBRA: "Vertebra",
    PieceRole.SKULL: "Skull plate",
    PieceRole.JAW: "Lower jaw",
    PieceRole.LIMB: "Limb bone",
    PieceRole.SPACER: "Spacer",
    PieceRole.CONNECTOR: "Bowtie connector",
}


@dataclass
class Step:
    title: str
    text: str
    items: list[RenderItem]
    new: list[str]


def _direction_phrase(vec: np.ndarray) -> str:
    """Human description of a display-frame direction (where a piece comes from)."""
    x, y = float(vec[0]), float(vec[1])
    ang = math.degrees(math.atan2(y, x))  # 90 = from above
    names = [(90, "from above"), (-90, "from below"), (0, "from the right"), (180, "from the left"),
             (-180, "from the left"), (45, "from above right"), (135, "from above left"),
             (-45, "from below right"), (-135, "from below left")]
    return min(names, key=lambda n: abs(ang - n[0]))[1]


def _groups(model: PuzzleModel) -> dict[str, str]:
    """Sub-assemblies: pieces joined by bowtie connectors (piece id -> group root)."""
    parent = {pc.id: pc.id for pc in model.pieces}

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for conn in model.analysis.get("connectors", []):
        ids = [conn["id"], *conn["pieces"]]
        for other in ids[1:]:
            if other in parent and conn["id"] in parent:
                parent[find(other)] = find(conn["id"])
    spines = [pc.id for pc in model.pieces if pc.role == PieceRole.SPINE]
    for sid in spines[1:]:
        parent[find(sid)] = find(spines[0])
    return {pid: find(pid) for pid in parent}


def _join_text(pieces: list[Piece]) -> str:
    keys = [p.id for p in pieces if p.role == PieceRole.CONNECTOR]
    hosts = [p.id for p in pieces if p.role != PieceRole.CONNECTOR]
    if not keys:
        return ""
    return (f"Lay {', '.join(hosts)} flat edge to edge and press bowtie connector{'s' if len(keys) > 1 else ''} "
            f"{', '.join(keys)} into the matching pocket{'s' if len(keys) > 1 else ''} across each seam.")


def build_steps(model: PuzzleModel) -> list[Step]:
    """Assembly order that respects every slot.

    Start with the backbone (all backbone segments, joined by their bowtie connectors).
    Insert cross pieces one at a time, beginning with the one through the middle of the body
    and moving outwards. Each cross piece slides in along its own trace and engages every
    plate that is already in place. A plate (or a bowtie-joined sub-assembly of plates, e.g.
    a limb or a skull side with its jaw) that is not placed yet is pre-assembled and slid
    onto the first cross piece it meets, right after that piece - so when later pieces
    arrive, all the plates they must engage are already held in position.
    """
    group = _groups(model)
    members: dict[str, list[Piece]] = {}
    for pc in model.pieces:
        members.setdefault(group[pc.id], []).append(pc)
    spine = [pc for pc in model.pieces if pc.role == PieceRole.SPINE]
    base = members[group[spine[0].id]]
    crosses = [pc for pc in model.pieces if pc.kind == PieceKind.CROSS]
    engages: dict[str, list[str]] = {pc.id: [] for pc in model.pieces}
    for j in model.joints:
        engages[j.cross_id].append(j.profile_id)
        engages[j.profile_id].append(j.cross_id)
    h = model.object_size_mm[1]

    def centre(pc: Piece) -> np.ndarray:
        c = pc.polygon.centroid
        return np.array(pc.transform.apply(c.x, c.y))

    body = np.mean([centre(pc) for pc in crosses], axis=0) if crosses else np.zeros(3)
    order = sorted(crosses, key=lambda pc: (float(np.linalg.norm(centre(pc) - body)), pc.id))

    names = ", ".join(p.id for p in base if p.role == PieceRole.SPINE)
    text = (f"{names} {'is' if len(spine) == 1 else 'form'} the central backbone. "
            + (_join_text(base) + " " if len(base) > 1 else "")
            + "Stand it upright; everything else interlocks with it.")
    steps = [Step(f"Step 1 - {names}: the backbone", text,
                  [RenderItem(p, color=HIGHLIGHT_COLOR) for p in base], [p.id for p in base])]
    placed: list[Piece] = list(base)
    placed_ids = {p.id for p in base}
    for cross in order:
        ins = insertion_vector(cross, model)
        held = [pid for pid in engages[cross.id] if pid in placed_ids]
        items = [RenderItem(p, color=GHOST_COLOR) for p in placed]
        items.append(RenderItem(cross, tuple(float(v) for v in ins * 0.3 * h), HIGHLIGHT_COLOR))
        kind = ROLE_NAMES[cross.role].lower()
        steps.append(Step(
            f"Step {len(steps) + 1} - add {cross.id}",
            f"Slide the {kind} {cross.id} ({cross.region}) {_direction_phrase(ins)} into "
            f"{', '.join(held) or 'place'} until the slots meet.",
            items, [cross.id],
        ))
        placed.append(cross)
        placed_ids.add(cross.id)
        roots = []
        for pid in engages[cross.id]:
            if pid not in placed_ids and group[pid] not in roots:
                roots.append(group[pid])
        if roots:
            newcomers = [p for r in roots for p in members[r] if p.id not in placed_ids]
            for r in roots:
                sub = [p for p in members[r] if p.id not in placed_ids]
                if len(sub) > 1:
                    steps.append(Step(
                        f"Step {len(steps) + 1} - pre-assemble {' + '.join(p.id for p in sub if p.role != PieceRole.CONNECTOR)}",
                        _join_text(sub), [RenderItem(p, color=HIGHLIGHT_COLOR) for p in sub], [p.id for p in sub],
                    ))
            items = [RenderItem(p, color=GHOST_COLOR) for p in placed]
            items += [RenderItem(p, tuple(float(v) for v in -ins * 0.3 * h), HIGHLIGHT_COLOR) for p in newcomers]
            names = " and ".join(p.id for p in newcomers if p.role != PieceRole.CONNECTOR)
            kinds = sorted({ROLE_NAMES[p.role].lower() for p in newcomers if p.role != PieceRole.CONNECTOR})
            steps.append(Step(
                f"Step {len(steps) + 1} - attach {names}",
                f"Slide the {' / '.join(kinds)} {names} onto {cross.id} {_direction_phrase(-ins)}. "
                f"Later pieces lock {'them' if len(newcomers) > 1 else 'it'} in place.",
                items, [p.id for p in newcomers],
            ))
            placed.extend(newcomers)
            placed_ids.update(p.id for p in newcomers)
    leftovers = [p for p in model.pieces if p.id not in placed_ids]
    if leftovers:  # pieces without any joint never happen (validation), kept for safety
        placed.extend(leftovers)
    steps.append(Step(
        f"Step {len(steps) + 1} - done",
        "Check that every joint is fully seated and all edges sit flush. A drop of wood glue in each slot "
        "and under each bowtie makes the model permanent.",
        [RenderItem(p) for p in model.pieces], [],
    ))
    return steps


# ---------------------------------------------------------------------------- drawing helpers


def _header(c: canvas.Canvas, title: str, page: int) -> None:
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN, PAGE_H - MARGIN, title)
    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN, f"PuzzleCut AI  ·  page {page}")
    c.setStrokeColor(RULE)
    c.line(MARGIN, PAGE_H - MARGIN - 6, PAGE_W - MARGIN, PAGE_H - MARGIN - 6)


def _image(c: canvas.Canvas, img, x: float, y: float, w: float, h: float) -> None:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), x, y, w, h, preserveAspectRatio=True, anchor="c")


def _table(c: canvas.Canvas, rows: list[tuple[str, str]], x: float, y: float, col: float, line: float = 13) -> float:
    for k, v in rows:
        c.setFont("Helvetica", 9)
        c.setFillColor(MUTED)
        c.drawString(x, y, k)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + col, y, v)
        y -= line
    return y


def _wrap(c: canvas.Canvas, text: str, x: float, y: float, width: float, size: int = 9, leading: float = 12,
          font: str = "Helvetica") -> float:
    c.setFont(font, size)
    words, line = text.split(), ""
    for word in words:
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, font, size) > width:
            c.drawString(x, y, line)
            y -= leading
            line = word
        else:
            line = trial
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def _draw_shape(c: canvas.Canvas, piece: Piece, x: float, y: float, w: float, h: float) -> None:
    minx, miny, maxx, maxy = piece.polygon.bounds
    pw, ph = maxx - minx, maxy - miny
    s = min(w / pw, h / ph)
    ox = x + (w - pw * s) / 2 - minx * s
    oy = y + (h - ph * s) / 2 - miny * s
    path = c.beginPath()
    for ring in [piece.polygon.exterior] + list(piece.polygon.interiors):
        pts = list(ring.coords)
        path.moveTo(ox + pts[0][0] * s, oy + pts[0][1] * s)
        for px, py in pts[1:]:
            path.lineTo(ox + px * s, oy + py * s)
        path.close()
    c.setFillColor(colors.HexColor("#F3E8D8"))
    c.setStrokeColor(colors.HexColor("#92400E"))
    c.setLineWidth(0.6)
    c.drawPath(path, stroke=1, fill=1, fillMode=0)
    c.setStrokeColor(colors.HexColor("#2563EB"))
    c.setLineWidth(0.4)
    for stroke in piece.engrave:
        if len(stroke) >= 2:
            p = c.beginPath()
            p.moveTo(ox + stroke[0][0] * s, oy + stroke[0][1] * s)
            for px, py in stroke[1:]:
                p.lineTo(ox + px * s, oy + py * s)
            c.drawPath(p, stroke=1, fill=0)


def _piece_card(c, model: PuzzleModel, piece: Piece, conn: list[str], locator, x, y, w, h) -> None:
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 6, stroke=1, fill=0)
    shape_w = w * 0.42
    _draw_shape(c, piece, x + 6, y + 30, shape_w - 6, h - 40)
    _image(c, locator, x + shape_w + 2, y + 30, w - shape_w - 8, h - 40)
    minx, miny, maxx, maxy = piece.polygon.bounds
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(x + 6, y + 18, piece.id)
    c.setFont("Helvetica", 6.8)
    c.setFillColor(MUTED)
    engraved = f" · engraved {piece.label}" if piece.label and piece.label != piece.id else ""
    c.drawString(x + 6 + c.stringWidth(piece.id, "Helvetica-Bold", 9.5) + 6, y + 18,
                 f"{ROLE_NAMES[piece.role]} · {piece.region} · {maxx - minx:.0f}×{maxy - miny:.0f} mm{engraved}")
    joins = ", ".join(conn) if conn else "-"
    text = f"Connects to: {joins}"
    if c.stringWidth(text, "Helvetica", 6.8) > w - 12:
        while c.stringWidth(text + "…", "Helvetica", 6.8) > w - 12 and len(text) > 10:
            text = text[:-1]
        text += "…"
    c.drawString(x + 6, y + 8, text)


# ---------------------------------------------------------------------------- document


def export_assembly_guide(model: PuzzleModel, layout: Layout, sheet: SheetGeometry, material: str,
                          quality: dict | None = None) -> bytes:
    try:
        return _build(model, layout, sheet, material, quality or {})
    except PuzzleError:
        raise
    except Exception as exc:
        raise PuzzleError(ErrorCode.PDF_EXPORT_FAILED, "Assembly guide generation failed.", repr(exc)) from exc


def _build(model: PuzzleModel, layout: Layout, sheet: SheetGeometry, material: str, quality: dict) -> bytes:
    p = model.params
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle("PuzzleCut AI assembly guide")
    c.setAuthor("PuzzleCut AI")
    page = 1
    content_w = PAGE_W - 2 * MARGIN
    conn = connections(model)

    # ---- 1. overview -------------------------------------------------------------------------
    _header(c, "Assembly Guide", page)
    hero = render([RenderItem(pc) for pc in model.pieces], p.thickness_mm, (1400, 1000)).image
    _image(c, hero, MARGIN, PAGE_H / 2 - 30, content_w, PAGE_H / 2 - MARGIN - 20)
    w, h, d = model.object_size_mm
    roles: dict[PieceRole, int] = {}
    for pc in model.pieces:
        roles[pc.role] = roles.get(pc.role, 0) + 1
    structure = ", ".join(f"{n} {ROLE_NAMES[r].lower()}" for r, n in roles.items())
    rows = [
        ("Pieces", f"{len(model.pieces)}: {structure}"),
        ("Material", f"{material}, {p.thickness_mm:g} mm"),
        ("Assembled size", f"{w:.0f} × {h:.0f} × {d:.0f} mm (W × H × D)"),
        ("Slot width (as cut)", f"{p.slot_width_mm:.2f} mm = thickness {p.thickness_mm:g} + clearance {p.clearance_mm:g}"),
        ("Kerf compensation", f"{p.kerf_mm:g} mm (tool path offset {p.kerf_mm / 2:g} mm outward)"),
        ("Joints", f"{len(model.joints)} half-lap joints ({sum(j.load_bearing for j in model.joints)} load-bearing)"),
        ("Sheet", f"{layout.sheet_width_mm:g} × {layout.sheet_height_mm:g} mm, waste {layout.waste_percentage:.1f}%"),
    ]
    if quality:
        rows.append(("Quality", f"narrowest feature {quality.get('min_feature_mm')} mm, thinnest slot wall "
                                f"{quality.get('min_slot_wall_mm')} mm, closest cuts {quality.get('min_cut_distance_mm')} mm"))
    y = _table(c, rows, MARGIN, PAGE_H / 2 - 50, 105)
    y -= 6
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, y, "Before you start")
    _wrap(c,
          "Cut the RED lines and engrave (or lightly score) the BLUE labels. Test-fit one joint first: if it is too "
          "tight, increase clearance; if too loose, decrease it. LONG pieces run along the body, CROSS ribs go "
          "across it, DETAIL plates add volume to individual regions such as the head. The 3D shape is a stylized "
          "interpretation of the silhouette - depth is estimated, not measured.",
          MARGIN, y - 14, content_w)
    c.showPage()
    page += 1

    # ---- 2. parts overview: the real sheet ----------------------------------------------------------
    _header(c, "Parts Overview", page)
    scale = min(content_w / layout.sheet_width_mm, (PAGE_H - 2 * MARGIN - 60) / layout.sheet_height_mm)
    sx = MARGIN + (content_w - layout.sheet_width_mm * scale) / 2
    sy = PAGE_H - MARGIN - 24 - layout.sheet_height_mm * scale
    c.setStrokeColor(RULE)
    c.rect(sx, sy, layout.sheet_width_mm * scale, layout.sheet_height_mm * scale)
    draw_sheet(c, sheet, sx, sy, scale)
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(INK)
    for sp in sheet.pieces:
        if sp.label_anchor is None:  # the kit frame has no ID tag
            continue
        ax, ay = sp.label_anchor
        c.drawCentredString(sx + ax * scale, sy + ay * scale - 2, sp.id)
    _wrap(c, f"{len(model.pieces)} pieces on a {layout.sheet_width_mm:g} × {layout.sheet_height_mm:g} mm sheet "
             f"(true shapes, reduced scale). Red = cut, blue = engraved label.", MARGIN, sy - 14, content_w)
    c.showPage()
    page += 1

    # ---- 3. piece identification ---------------------------------------------------------------
    _header(c, "Piece Identification", page)
    cols, gap, card_h = 2, 8.0, 118.0
    card_w = (content_w - gap) / cols
    y_top, slot = PAGE_H - MARGIN - 20, 0
    for pc in model.pieces:
        r, col = divmod(slot, cols)
        cy = y_top - (r + 1) * card_h - r * gap
        if cy < MARGIN:
            c.showPage()
            page += 1
            _header(c, "Piece Identification (continued)", page)
            slot, r, col = 0, 0, 0
            cy = y_top - card_h
        items = [RenderItem(o, color=HIGHLIGHT_COLOR if o.id == pc.id else GHOST_COLOR) for o in model.pieces]
        locator = render(items, p.thickness_mm, (420, 300), supersample=1, padding=0.06).image
        _piece_card(c, model, pc, conn[pc.id], locator, MARGIN + col * (card_w + gap), cy, card_w, card_h)
        slot += 1
    c.showPage()
    page += 1

    # ---- 4. exploded view + slot relationships -------------------------------------------------------
    _header(c, "Exploded View & Slot Relationships", page)
    exploded = render(exploded_items(model), p.thickness_mm, (1400, 900), label=True, camera=Camera(35, 22)).image
    _image(c, exploded, MARGIN, PAGE_H / 2 - 10, content_w, PAGE_H / 2 - MARGIN - 20)
    y = PAGE_H / 2 - 28
    headers = ["Joint", "Long piece", "Cross rib", "Grip (mm)", "Load-bearing"]
    xs = [MARGIN + k * content_w / len(headers) for k in range(len(headers))]
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(INK)
    for hx, hd in zip(xs, headers):
        c.drawString(hx, y, hd)
    y -= 12
    c.setFont("Helvetica", 7.5)
    for j in model.joints:
        if y < MARGIN:
            c.showPage()
            page += 1
            _header(c, "Slot Relationships (continued)", page)
            y = PAGE_H - MARGIN - 26
            c.setFont("Helvetica", 7.5)
        vals = [j.id, j.profile_id, j.cross_id, f"{j.engagement_mm:.1f}", "yes" if j.load_bearing else "clearance"]
        c.setFillColor(INK)
        for hx, v in zip(xs, vals):
            c.drawString(hx, y, v)
        y -= 10
    c.showPage()
    page += 1

    # ---- 5. assembly sequence, two stages per page -----------------------------------------------
    steps = build_steps(model)
    slot_h = (PAGE_H - 2 * MARGIN - 30) / 2
    for i, step in enumerate(steps):
        if i % 2 == 0:
            if i:
                c.showPage()
                page += 1
            _header(c, f"Assembly Sequence ({len(steps)} stages)", page)
        top = PAGE_H - MARGIN - 20 - (i % 2) * slot_h
        img = render(step.items, p.thickness_mm, (1000, 600), label=bool(step.new), label_ids=set(step.new),
                     camera=Camera(35, 22)).image
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(MARGIN, top - 4, step.title)
        c.setFillColor(INK)
        ty = _wrap(c, step.text, MARGIN, top - 18, content_w)
        _image(c, img, MARGIN, top - slot_h + 6, content_w, ty - (top - slot_h + 6) - 4)
    c.showPage()
    c.save()
    return buf.getvalue()
