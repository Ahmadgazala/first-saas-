# Lion silhouette traced from the photo (pixel coords, y down, 559x357 image)
import math
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union
from shapely import affinity

def ragged_mane(u, mask, period=9.0, amp=8.0):
    """zig-zag the outline of u (outward) wherever it lies inside `mask` -> hairy mane"""
    u = u.segmentize(1.5)
    c = list(u.exterior.coords)[:-1]
    n = len(c); out = []; s = 0.0
    for i in range(n):
        p = c[i]; a = c[i - 1]; b = c[(i + 1) % n]
        s += math.hypot(p[0] - a[0], p[1] - a[1])
        tx, ty = b[0] - a[0], b[1] - a[1]
        l = math.hypot(tx, ty) or 1
        nx, ny = ty / l, -tx / l
        if u.contains(Point(p[0] + nx * 1.2, p[1] + ny * 1.2)): nx, ny = -nx, -ny
        if mask.contains(Point(p)):
            ph = (s % period) / period
            tri = 1 - abs(2 * ph - 1) * 2 if False else (ph * 2 if ph < 0.5 else (1 - ph) * 2)   # 0..1..0
            d = amp * tri
            out.append((p[0] + nx * d, p[1] + ny * d))
        else:
            out.append(p)
    return Polygon(out).buffer(0)

def lion_px():
    torso = Polygon([(346,92),(390,90),(414,88),(440,92),(460,100),(472,112),(483,128),(487,150),(484,172),(478,200),
                     (462,215),(430,205),(412,190),(394,194),(372,208),(350,214),(330,217),(310,216),(300,226),(300,150)])
    head = Polygon([(186,160),(184,150),(183,138),(180,120),(175,105),(174,92),(178,76),(190,62),(210,52),(232,49),
                    (252,52),(277,54),(297,54),(316,64),(335,80),(346,98),(350,112),(344,140),(330,175),(318,200),
                    (300,222),(272,238),(258,246),(242,244),(234,230),(229,212),(222,200),(210,196),(198,192),
                    (194,184),(196,176),(188,168)])
    fl_near = Polygon([(262,222),(300,222),(291,252),(278,273),(267,293),(258,307),(255,328),(251,337),(217,334),
                       (222,322),(238,300),(244,280),(253,260),(259,240)])
    fl_far  = Polygon([(300,222),(330,222),(332,262),(335,290),(338,320),(333,331),(300,332),(298,326),(300,322),
                       (317,307),(315,293),(307,273),(300,248)])
    hl_near = Polygon([(410,186),(422,198),(424,220),(428,242),(434,268),(440,285),(436,300),(420,310),(407,320),
                       (409,330),(416,332),(432,331),(440,326),(448,312),(452,295),(457,255),(460,230),(478,200),
                       (484,172),(470,160),(440,170)])
    hl_far  = Polygon([(462,200),(486,195),(484,212),(495,240),(510,270),(520,305),(527,322),(526,331),(508,331),
                       (505,315),(490,285),(472,255),(462,225)])
    tail = Polygon([(484,150),(497,158),(501,205),(511,230),(526,249),(540,257),(550,263),(559,268),(559,286),
                    (548,290),(538,285),(534,276),(516,258),(500,234),(488,205),(481,172)])
    u = unary_union([torso, head, fl_near, fl_far, hl_near, hl_far, tail])
    u = u.buffer(5).buffer(-8).buffer(3)            # smooth joints / round corners
    # open mouth: wedge cut into the muzzle
    mouth = Polygon([(176,168),(208,176),(204,180),(176,183)])
    u = u.difference(mouth).buffer(-1.0).buffer(1.0)
    # hairy mane (not the face, not the legs)
    mane_mask = head.buffer(2).intersection(box(205, 30, 370, 232)).difference(box(150, 100, 224, 260))
    mane_mask = mane_mask.union(head.buffer(2).intersection(box(205, 30, 370, 75)))
    u = ragged_mane(u, mane_mask, period=11.0, amp=7.0)
    if u.geom_type != 'Polygon': u = max(u.geoms, key=lambda g: g.area)
    return u

def eye_px():
    e = affinity.scale(Point(0, 0).buffer(1, 24), 6.0, 2.7)
    e = affinity.rotate(e, -12)
    return affinity.translate(e, 217, 125)
