import math
from shapely.geometry import Polygon, LineString, Point, box, MultiLineString
from shapely.ops import unary_union, linemerge
from shapely import affinity
import ezdxf
from shape import lion_px, eye_px

# ---------------- parameters (mm) ----------------
T      = 3.0      # material thickness
FIT    = 0.0      # extra slot width (add 0.1..0.2 if your parts fit too tight)
SCALE  = 0.78     # px -> mm  (lion ~299 mm long)
TAB    = 3.0      # width of the little bridges that hold parts on the frame
BAR    = 12.0     # frame bar width
SLOT   = T + FIT
G      = 7.0      # gap between parts on the sheet

# ---------------- profile (side view) ----------------
g = lion_px(); eye = eye_px()
x0, y0, x1, y1 = g.bounds
tr = lambda geom: affinity.scale(affinity.translate(geom, -x0, -y0), SCALE, SCALE, origin=(0, 0))
P0 = tr(g).difference(tr(eye))
GROUND = P0.bounds[3]
X = lambda px: (px - x0) * SCALE
Y = lambda px: (px - y0) * SCALE

# toe notches cut into the paws (px x-positions of the notches)
for xp in (228, 240, 311, 324, 418, 429, 514, 521):
    xm = X(xp)
    P0 = P0.difference(Polygon([(xm - 1.1, GROUND + 1), (xm + 1.1, GROUND + 1), (xm, GROUND - 6)]))

def col_top(poly, x):
    return poly.intersection(LineString([(x, -50), (x, 1000)])).bounds[1]

def egg(cx, cy, a, b, bias=0.10, n=64):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        pts.append((cx + a * math.cos(t) * (1 + bias * math.sin(t)), cy + b * math.sin(t)))
    return Polygon(pts)

def wavy_ellipse(cx, cy, a, b, amp, n):
    pts = []
    for i in range(240):
        t = 2 * math.pi * i / 240
        k = 1 + amp * abs(math.sin(n * t / 2))
        pts.append((cx + a * k * math.cos(t), cy + b * k * math.sin(t)))
    return Polygon(pts)

# rib specs: x(px), bottom(px) [or ('top', px-height)], H factor, W factor, kind
RIBS = [
    (200, 190, 1.10, 0.50, 'plain'),     # face / cheeks
    (234, 243, 1.04, 0.40, 'mane'),
    (256, ('top', 48), 1.00, 0.95, 'ears'),
    (278, 240, 1.04, 0.44, 'mane'),
    (300, 228, 1.04, 0.46, 'mane'),
    (330, 226, 1.10, 0.52, 'legs'),      # shoulders + front legs
    (364, 214, 1.12, 0.58, 'plain'),     # chest
    (400, 200, 1.12, 0.60, 'plain'),     # belly
    (438, 205, 1.10, 0.52, 'legs'),      # hips + hind legs
    (474, 198, 1.10, 0.52, 'plain'),     # rump
]

P = P0
slot_info = []        # (id, xc, yt, ym)
parts = {}
rib_eng = {}          # engraved details in each rib's local coords
kinds = {}
checks = []
for i, (xpx, ybpx, hf, wf, kind) in enumerate(RIBS):
    pid = i + 2
    xc = X(xpx)
    yt = col_top(P0, xc)
    yb = yt + ybpx[1] * SCALE if isinstance(ybpx, tuple) else Y(ybpx)
    ym = (yt + yb) / 2
    H = hf * (yb - yt); W = wf * H
    P = P.difference(box(xc - SLOT / 2, yt - 8, xc + SLOT / 2, ym))
    slot_info.append((pid, xc, yt, ym)); kinds[pid] = kind
    eng = []
    if kind == 'mane':
        body = wavy_ellipse(0, ym, W / 2, H / 2, 0.04, 15)
        for k in range(30):                               # engraved hair strokes
            a = 2 * math.pi * k / 30
            ca, sa = math.cos(a), math.sin(a)
            r1, r2 = 0.62, 0.82
            eng.append([(ca * W / 2 * r1, ym + sa * H / 2 * r1), (ca * W / 2 * r2, ym + sa * H / 2 * r2)])
    elif kind == 'ears':
        body = Point(0, ym).buffer(1, 48)
        body = affinity.scale(body, W / 2, H / 2, origin=(0, ym))
        for s in (-1, 1):
            ear = Point(s * W * 0.40, ym - H * 0.28).buffer(9.5, 32)
            body = body.union(ear)
            inner = affinity.scale(Point(s * W * 0.40, ym - H * 0.28).buffer(1, 24), 4.2, 5.2, origin=(s * W * 0.40, ym - H * 0.28))
            eng.append(list(inner.exterior.coords))
        body = body.buffer(2).buffer(-2)
    else:
        body = egg(0, ym, W / 2, H / 2, 0.10)
        if kind == 'plain' and W > 40:
            ins = affinity.scale(Point(0, ym).buffer(1, 64), W / 2 - 5, H / 2 - 5, origin=(0, ym))
            ins = ins.exterior.difference(box(-SLOT / 2 - 2.5, ym - 1, SLOT / 2 + 2.5, GROUND + 20))
            eng += [list(q.coords) for q in (ins.geoms if hasattr(ins, 'geoms') else [ins])]
    if kind == 'legs':
        z0 = W * 0.33; hw = 8.0
        for s in (-1, 1):
            yl = ym + 0.1 * H; yk = GROUND - 34
            leg = Polygon([(s * (z0 - 8), yl), (s * (z0 + 8), yl), (s * (z0 + 6), yk), (s * (z0 + 11), GROUND - 16),
                           (s * (z0 + 13), GROUND - 6), (s * (z0 + 11), GROUND), (s * (z0 - 11), GROUND),
                           (s * (z0 - 12), GROUND - 7), (s * (z0 - 7), GROUND - 17), (s * (z0 - 6), yk)])
            body = body.union(leg)
        body = body.buffer(2).buffer(-2)
        for s in (-1, 1):                                  # toe notch on each foot
            for dz in (-4, 4):
                zc = s * z0 + dz
                body = body.difference(Polygon([(zc - 1.1, GROUND + 1), (zc + 1.1, GROUND + 1), (zc, GROUND - 6)]))
    rib = body.difference(box(-SLOT / 2, ym, SLOT / 2, GROUND + 20))
    parts[pid] = rib; rib_eng[pid] = eng
    py = P.intersection(LineString([(xc, -10), (xc, GROUND + 10)]))
    ry = rib.intersection(LineString([(0, -10), (0, GROUND + 10)]))
    checks.append((pid, round(py.intersection(ry).length, 4)))

if P.geom_type != 'Polygon':
    print('profile pieces:', [(round(q.area,1), [round(v,1) for v in q.bounds]) for q in P.geoms])
    P = max(P.geoms, key=lambda q: q.area)      # drop slivers cut loose by the slots
parts[1] = P
print('interference (mm of overlap, must be 0):', checks)
for k in sorted(parts):
    v = parts[k]; b = v.bounds
    print('part', k, kinds.get(k, 'profile'), 'size %.0f x %.0f' % (b[2] - b[0], b[3] - b[1]), v.geom_type, v.is_valid)
assert all(c[1] == 0 for c in checks)

# ---------------- engraved details on the profile ----------------
prof_eng = []
cx_, cy_ = X(262), Y(150)
slots_poly = unary_union([box(xc - SLOT / 2 - 1.5, yt - 10, xc + SLOT / 2 + 1.5, ym + 1.5) for _, xc, yt, ym in slot_info])
label_boxes = []
safe = P.buffer(-3.0).difference(slots_poly)
def curve(c, th, r1, r2, bend):
    pts = []
    for j in range(7):
        t = j / 6; r = r1 + (r2 - r1) * t; a = th + bend * t * t
        pts.append((c[0] + math.cos(a) * r, c[1] + math.sin(a) * r))
    return pts
mane_strokes = []
for ring, (r1, r2) in enumerate(((50, 76), (60, 86))):
    n = 24
    for k in range(n):
        th = math.radians(196 + (k + 0.5 * ring) * (252 / n))
        pts = [(X(262) + (px - 0) , 0) for px in ()]
        c = (X(262), Y(150))
        mane_strokes.append(curve(c, th, mm_r1 := r1 * SCALE, r2 * SCALE, 0.25))
body_lines = [   # muscle / fur lines (px)
    [(436, 130), (452, 150), (447, 182)],           # thigh
    [(448, 112), (462, 140), (462, 172)],
    [(318, 160), (306, 188), (292, 214)],           # shoulder
    [(334, 146), (324, 176), (312, 206)],
    [(370, 150), (378, 172), (372, 196)],           # ribs
    [(392, 146), (400, 168), (394, 192)],
    [(510, 222), (498, 252), (502, 282)],
]
for ln in body_lines: mane_strokes.append([(X(a), Y(b)) for a, b in ln])
# nose, nostril, eyebrow
mane_strokes.append([(X(180), Y(150)), (X(185), Y(146)), (X(190), Y(152)), (X(185), Y(158)), (X(180), Y(150))])
mane_strokes.append([(X(200), Y(116)), (X(212), Y(113)), (X(224), Y(118))])
# tail tuft strands
for k in range(5):
    mane_strokes.append([(X(520 + 7 * k), Y(268 + k * 0.5)), (X(530 + 6 * k), Y(279))])
face_zone = box(X(150), Y(100), X(232), Y(260))
for ls in list(mane_strokes):
    if LineString(ls).intersects(face_zone) and ls not in mane_strokes[-8:]: mane_strokes.remove(ls)
lab_boxes = [box(xc + 1.0, yt + 10.5, xc + 11, yt + 18.5) for _, xc, yt, ym in slot_info] + [box(X(412) - 1, Y(142) - 1, X(412) + 6, Y(142) + 9)]
safe = safe.difference(unary_union(lab_boxes))
for ls in mane_strokes:
    ls_geom = LineString(ls)
    clipped = ls_geom.intersection(safe if ls_geom.length < 400 else ls_geom)
    geoms = [clipped] if clipped.geom_type == 'LineString' else [h for h in getattr(clipped, 'geoms', []) if h.geom_type == 'LineString']
    for h in geoms:
        if h.length > 2.0: prof_eng.append(list(h.coords))
print('profile engraving strokes:', len(prof_eng))

# ---------------- layout on the sprue frame ----------------
def place(poly, x, y, rot=0, extra=()):
    """rotate about centre, move bbox corner to (x,y); returns placed poly + transform for any geometry"""
    c = poly.centroid
    rp = affinity.rotate(poly, rot, origin=(c.x, c.y)) if rot else poly
    b = rp.bounds; dx, dy = x - b[0], y - b[1]
    f = lambda gm: affinity.translate(affinity.rotate(gm, rot, origin=(c.x, c.y)) if rot else gm, dx, dy)
    return affinity.translate(rp, dx, dy), f

ox = BAR + G; oy = BAR + G
placed = {}; xform = {}
placed[1], xform[1] = place(parts[1], ox, oy)
legs = [pid for pid in parts if kinds.get(pid) == 'legs']
rx = placed[1].bounds[2] + G
for pid in legs:
    placed[pid], xform[pid] = place(parts[pid], rx, oy)
    rx = placed[pid].bounds[2] + G
row1_bottom = max(p.bounds[3] for p in placed.values())
Wlimit = max(rx - G + 14, 505)
others = [pid for pid in parts if pid != 1 and pid not in legs]
rot_polys = {pid: affinity.rotate(parts[pid], 90, origin='centroid') for pid in others}
others.sort(key=lambda pid: -(rot_polys[pid].bounds[2] - rot_polys[pid].bounds[0]))
cx = ox; cy = row1_bottom + G; rowh = 0
for pid in others:
    b = rot_polys[pid].bounds; w = b[2] - b[0]; h = b[3] - b[1]
    if cx + w > Wlimit:
        cx = ox; cy += rowh + G; rowh = 0
    placed[pid], xform[pid] = place(parts[pid], cx, cy, rot=90)
    cx += w + G; rowh = max(rowh, h)
Wf = max(p.bounds[2] for p in placed.values()) + G + BAR
Hf = max(p.bounds[3] for p in placed.values()) + G + BAR + 4
print('sheet %.0f x %.0f mm' % (Wf, Hf))
inner = box(BAR, BAR, Wf - BAR, Hf - BAR)
outer = box(0, 0, Wf, Hf)
frame_bar = outer.difference(inner)

# ---------------- tabs ----------------
DIRS = [(0, -1), (-1, 0), (0, 1), (1, 0)]
tabs = []
def make_tabs(pid, poly, maxtabs=3):
    obstacles = {k: v for k, v in placed.items() if k != pid}; obstacles['frame'] = frame_bar
    made = 0; b = poly.bounds
    for d in DIRS:
        if made >= maxtabs: break
        for frac in (0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6, 0.1, 0.9):
            if d[0] == 0:
                x = b[0] + frac * (b[2] - b[0])
                inter = poly.intersection(LineString([(x, b[1] - 1), (x, b[3] + 1)]))
                if inter.is_empty: continue
                S = (x, inter.bounds[1] if d[1] < 0 else inter.bounds[3])
            else:
                y = b[1] + frac * (b[3] - b[1])
                inter = poly.intersection(LineString([(b[0] - 1, y), (b[2] + 1, y)]))
                if inter.is_empty: continue
                S = (inter.bounds[0] if d[0] < 0 else inter.bounds[2], y)
            back = LineString([S, (S[0] - d[0] * 2.0, S[1] - d[1] * 2.0)]).buffer(TAB / 2, cap_style=2)
            if not poly.buffer(1.2).contains(back): continue
            ray = LineString([S, (S[0] + d[0] * 40, S[1] + d[1] * 40)])
            best = None
            for k, o in obstacles.items():
                hit = ray.intersection(o)
                if hit.is_empty: continue
                dist = Point(S).distance(hit)
                if best is None or dist < best[0]: best = (dist, k)
            if best is None or best[0] > 22: continue
            end = (S[0] + d[0] * (best[0] + 1.0), S[1] + d[1] * (best[0] + 1.0))
            start = (S[0] - d[0] * 1.0, S[1] - d[1] * 1.0)
            rect = LineString([start, end]).buffer(TAB / 2, cap_style=2)
            if any(rect.intersects(o) for k, o in obstacles.items() if k != best[1]): continue
            tabs.append(rect); made += 1
            break
    return made
for pid, poly in placed.items():
    n = make_tabs(pid, poly)
    if n < 2: print('WARNING part', pid, 'only', n, 'tabs')
tabs_u = unary_union(tabs)

# ---------------- stroke font ----------------
SEG = {'a': ((0, 0), (1, 0)), 'b': ((1, 0), (1, 1)), 'c': ((1, 1), (1, 2)), 'd': ((0, 2), (1, 2)),
       'e': ((0, 1), (0, 2)), 'f': ((0, 0), (0, 1)), 'g': ((0, 1), (1, 1))}
DIG = {'0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc', '5': 'afgcd', '6': 'afgedc', '7': 'abc',
       '8': 'abcdefg', '9': 'abcdfg'}
LET = {'L': [[(0, 0), (0, 2), (1, 2)]], 'I': [[(0, 0), (1, 0)], [(0.5, 0), (0.5, 2)], [(0, 2), (1, 2)]],
       'O': [[(0, 0), (1, 0), (1, 2), (0, 2), (0, 0)]], 'N': [[(0, 2), (0, 0), (1, 2), (1, 0)]]}
def text_lines(s, x, y, h):
    w = h * 0.55; adv = w + h * 0.35; out = []
    for i, ch in enumerate(s):
        gx = x + i * adv
        strokes = [list(SEG[k]) for k in DIG[ch]] if ch in DIG else LET[ch]
        for st in strokes: out.append([(gx + px * w, y + py * h / 2) for px, py in st])
    return out
def text_w(s, h): return len(s) * (h * 0.55 + h * 0.35) - h * 0.35

engrave = []
# --- engraved details
for pid in parts:
    for ln in (rib_eng.get(pid, []) if pid != 1 else []):
        engrave.append(list(xform[pid](LineString(ln)).coords))
for ln in prof_eng:
    engrave.append(list(xform[1](LineString(ln)).coords))

# --- part numbers: find a spot well inside the material
def label_spot(poly, text, h):
    w = text_w(text, h); c = poly.centroid
    best = None
    for r in range(0, 60, 3):
        for a in range(0, 360, 30):
            x = c.x + r * math.cos(math.radians(a)) - w / 2; y = c.y + r * math.sin(math.radians(a)) - h / 2
            bx = box(x - 1.5, y - 1.5, x + w + 1.5, y + h + 1.5)
            if poly.buffer(-2.0).contains(bx):
                return x, y
    return c.x - w / 2, c.y - h / 2
for pid in parts:
    if pid == 1: continue
    x, y = label_spot(placed[pid], str(pid), 5)
    engrave += text_lines(str(pid), x, y, 5)
# profile: "1" and the matching number beside each slot
x, y = label_spot(placed[1].difference(box(0, 0, 300, 70)), '1', 6)   # keep clear of the mane
engrave += text_lines('1', ox + X(412), oy + Y(142), 6)
for pid, xc, yt, ym in slot_info:
    engrave += text_lines(str(pid), ox + xc + 2.6, oy + yt + 12, 4)
engrave += text_lines('LION', Wf - 95, Hf - BAR + 1.8, BAR - 3.6)

# ---------------- cut lines ----------------
def rings(poly):
    yield list(poly.exterior.coords)
    for r in poly.interiors: yield list(r.coords)
cut = []
def add_cut(coords):
    ls = LineString(coords).difference(tabs_u)
    if ls.is_empty: return
    geoms = [ls] if ls.geom_type == 'LineString' else list(ls.geoms)
    if len(geoms) > 1:
        m = linemerge(MultiLineString(geoms))
        geoms = [m] if m.geom_type == 'LineString' else list(m.geoms)
    for gg in geoms:
        if gg.geom_type == 'LineString': cut.append(list(gg.coords))
for poly in placed.values():
    for r in rings(poly): add_cut(r)
add_cut(list(inner.exterior.coords))
cut.append(list(outer.exterior.coords))

# ---------------- export SVG ----------------
def pts(c): return ' '.join('%.3f,%.3f' % p for p in c)
svg = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<svg xmlns="http://www.w3.org/2000/svg" width="%.2fmm" height="%.2fmm" viewBox="0 0 %.2f %.2f">' % (Wf, Hf, Wf, Hf),
       '<g id="CUT" fill="none" stroke="#ff0000" stroke-width="0.1" stroke-linejoin="round" stroke-linecap="round">']
for c in cut: svg.append('<polyline points="%s"/>' % pts(c))
svg.append('</g><g id="ENGRAVE" fill="none" stroke="#0000ff" stroke-width="0.1" stroke-linecap="round" stroke-linejoin="round">')
for c in engrave: svg.append('<polyline points="%s"/>' % pts(c))
svg.append('</g></svg>')
open('lion_puzzle.svg', 'w').write('\n'.join(svg))

# ---------------- export DXF (y flipped: DXF is y-up) ----------------
doc = ezdxf.new('R2010', setup=True); doc.units = ezdxf.units.MM
doc.layers.add('CUT', color=1); doc.layers.add('ENGRAVE', color=5)
msp = doc.modelspace()
fl = lambda c: [(x, Hf - y) for x, y in c]
for c in cut: msp.add_lwpolyline(fl(c), dxfattribs={'layer': 'CUT'})
for c in engrave: msp.add_lwpolyline(fl(c), dxfattribs={'layer': 'ENGRAVE'})
doc.saveas('lion_puzzle.dxf')

# ---------------- previews ----------------
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(12, 9))
for c in cut: ax.plot(*zip(*c), color='red', lw=0.8)
for c in engrave: ax.plot(*zip(*c), color='blue', lw=0.5)
ax.set_aspect('equal'); ax.invert_yaxis(); ax.set_title('Cut sheet  %.0f x %.0f mm  (red = cut, blue = engrave)' % (Wf, Hf))
fig.savefig('sheet_preview.png', dpi=120, bbox_inches='tight'); plt.close(fig)

from mpl_toolkits.mplot3d.art3d import Poly3DCollection
def draw3d(ax, elev, azim):
    def plate(poly, mapper, color):
        v = [mapper(x, y) for x, y in poly.exterior.coords]
        for r in poly.interiors: pass
        ax.add_collection3d(Poly3DCollection([v], facecolor=color, edgecolor='#5a3a10', linewidth=0.4, alpha=0.97))
    plate(parts[1], lambda x, y: (x, 0, -y), '#e8c48a')
    for pid, xc, yt, ym in slot_info:
        plate(parts[pid], lambda z, y, xc=xc: (xc, z, -y), '#b9863f' if kinds[pid] in ('mane', 'ears') else '#d9b072')
    ax.set_box_aspect((300, 120, 223)); ax.set_xlim(0, 300); ax.set_ylim(-60, 60); ax.set_zlim(-225, 0)
    ax.view_init(elev, azim); ax.set_axis_off()
fig = plt.figure(figsize=(15, 5.5))
for i, (e, a) in enumerate([(16, -62), (14, -125), (28, -35)]):
    ax = fig.add_subplot(1, 3, i + 1, projection='3d'); draw3d(ax, e, a)
fig.savefig('assembled_preview.png', dpi=100, bbox_inches='tight'); plt.close(fig)
