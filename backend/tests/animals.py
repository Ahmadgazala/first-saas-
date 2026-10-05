"""Deterministic side-view silhouettes of animals and objects for structural tests.

They are drawn from simple primitives but carry the features that matter for the engine:
heads with muzzles and ears, necks, torsos, four legs with paws/hooves, tails, antlers
(thin features), a trunk, a logo with a hole and an irregular blob.
"""

from __future__ import annotations

import cv2
import numpy as np

W, H = 1024, 768


def _canvas() -> np.ndarray:
    return np.zeros((H, W), dtype=np.uint8)


def _poly(m, pts):
    cv2.fillPoly(m, [np.array(pts, np.int32)], 255)


def _ell(m, c, ax, ang=0):
    cv2.ellipse(m, (int(c[0]), int(c[1])), (int(ax[0]), int(ax[1])), ang, 0, 360, 255, -1)


def _leg(m, top, bottom, w_top, w_bot, foot=0):
    (x0, y0), (x1, y1) = top, bottom
    _poly(m, [(x0 - w_top, y0), (x0 + w_top, y0), (x1 + w_bot, y1), (x1 - w_bot, y1)])
    if foot:
        _ell(m, (x1 + foot // 3, y1 - foot // 3), (w_bot + foot // 2, foot // 2))


def horse() -> np.ndarray:
    m = _canvas()
    _ell(m, (470, 380), (230, 105))  # barrel
    _ell(m, (640, 360), (100, 110))  # chest/shoulder
    _ell(m, (300, 370), (105, 105))  # hindquarters
    _poly(m, [(640, 300), (705, 160), (760, 130), (790, 175), (720, 330), (690, 420)])  # neck
    _ell(m, (800, 175), (70, 38), 35)  # head
    _ell(m, (845, 220), (38, 28), 35)  # muzzle
    _poly(m, [(760, 120), (770, 70), (790, 125)])  # ear
    _poly(m, [(735, 135), (742, 92), (762, 140)])  # ear
    for top, bot in [((650, 430), (665, 690)), ((600, 440), (590, 690)), ((340, 440), (330, 690)),
                     ((275, 430), (255, 690))]:
        _leg(m, top, bot, 24, 15, foot=26)
    _poly(m, [(205, 330), (170, 360), (150, 470), (170, 560), (200, 470), (225, 370)])  # tail
    return m


def lion() -> np.ndarray:
    m = _canvas()
    _ell(m, (470, 390), (240, 110))  # body
    _ell(m, (700, 330), (125, 145))  # mane
    _ell(m, (790, 330), (75, 65))  # head
    _ell(m, (850, 360), (45, 35))  # muzzle
    _ell(m, (745, 255), (22, 18))  # ear
    for top, bot in [((660, 450), (670, 690)), ((590, 460), (585, 690)), ((350, 450), (345, 690)),
                     ((280, 440), (270, 690))]:
        _leg(m, top, bot, 32, 22, foot=34)
    _poly(m, [(235, 360), (150, 420), (110, 520), (125, 525), (165, 430), (245, 385)])  # tail
    _ell(m, (115, 535), (26, 34), 20)  # tail tuft
    return m


def wolf() -> np.ndarray:
    m = _canvas()
    _ell(m, (470, 380), (220, 85))  # body
    _ell(m, (640, 365), (95, 95))  # chest
    _poly(m, [(650, 300), (730, 230), (770, 270), (700, 390)])  # neck
    _ell(m, (770, 245), (62, 48), -10)  # skull
    _poly(m, [(800, 225), (900, 260), (895, 280), (800, 290)])  # snout
    _poly(m, [(745, 205), (752, 140), (785, 205)])  # ears
    _poly(m, [(718, 215), (715, 150), (748, 205)])
    for top, bot in [((650, 430), (668, 680)), ((600, 430), (585, 680)), ((330, 420), (330, 680)),
                     ((275, 410), (262, 680))]:
        _leg(m, top, bot, 20, 12, foot=22)
    _poly(m, [(255, 350), (150, 430), (110, 520), (150, 515), (215, 440), (265, 395)])  # bushy tail
    _ell(m, (150, 470), (45, 60), 35)
    return m


def deer() -> np.ndarray:
    m = _canvas()
    _ell(m, (470, 400), (200, 85))  # body
    _ell(m, (620, 385), (80, 85))  # chest
    _poly(m, [(620, 330), (700, 190), (740, 205), (680, 380)])  # neck
    _ell(m, (735, 185), (55, 32), 25)  # head
    _ell(m, (775, 215), (28, 20), 25)
    _poly(m, [(705, 160), (680, 120), (712, 150)])  # ear
    # Antlers: thin branching features.
    for a, b in [((725, 160), (700, 60)), ((700, 60), (660, 30)), ((710, 100), (750, 55)),
                 ((735, 158), (770, 70)), ((770, 70), (800, 35)), ((752, 115), (720, 80))]:
        cv2.line(m, a, b, 255, 9)
    for top, bot in [((640, 440), (650, 700)), ((590, 445), (580, 700)), ((350, 440), (345, 700)),
                     ((295, 430), (285, 700))]:
        _leg(m, top, bot, 16, 8, foot=16)
    _poly(m, [(275, 360), (240, 350), (245, 390)])  # short tail
    return m


def elephant() -> np.ndarray:
    m = _canvas()
    _ell(m, (450, 360), (260, 170))  # body
    _ell(m, (720, 300), (120, 120))  # head
    _ell(m, (650, 320), (85, 125), -10)  # ear
    _poly(m, [(800, 330), (845, 420), (850, 560), (880, 640), (845, 650), (810, 560), (790, 420), (760, 360)])  # trunk
    _poly(m, [(800, 390), (850, 400), (845, 410), (795, 405)])  # tusk
    for top, bot in [((640, 440), (640, 690)), ((560, 470), (560, 690)), ((330, 470), (330, 690)),
                     ((250, 440), (250, 690))]:
        _leg(m, top, bot, 45, 42, foot=20)
    _poly(m, [(195, 330), (160, 420), (172, 425), (205, 350)])  # tail
    return m


def eagle() -> np.ndarray:
    """Perched eagle in side view: body, head with hooked beak, folded wing, tail, talons."""
    m = _canvas()
    _ell(m, (500, 380), (110, 190), 20)  # body
    _ell(m, (600, 190), (70, 62))  # head
    _poly(m, [(650, 170), (720, 185), (735, 225), (700, 215), (660, 215)])  # beak
    _poly(m, [(420, 250), (380, 520), (330, 690), (420, 640), (470, 480)])  # wing / tail
    _poly(m, [(330, 690), (300, 740), (360, 735), (410, 650)])
    _leg(m, (520, 540), (540, 640), 26, 18, foot=30)
    return m


def logo() -> np.ndarray:
    """A bold five-pointed star with a circular hole."""
    m = _canvas()
    c, r_out, r_in = (512, 400), 320, 135
    pts = []
    for k in range(10):
        ang = -np.pi / 2 + k * np.pi / 5
        r = r_out if k % 2 == 0 else r_in
        pts.append((c[0] + r * np.cos(ang), c[1] + r * np.sin(ang)))
    _poly(m, pts)
    cv2.circle(m, c, 50, 0, -1)
    return m


def irregular() -> np.ndarray:
    m = _canvas()
    rng = np.random.default_rng(11)
    ang = np.linspace(0, 2 * np.pi, 30, endpoint=False)
    rad = 260 + rng.uniform(-80, 80, len(ang))
    pts = np.stack([512 + rad * 1.25 * np.cos(ang), 390 + rad * 0.9 * np.sin(ang)], 1)
    _poly(m, pts)
    return ((cv2.GaussianBlur(m, (15, 15), 0) > 127) * 255).astype(np.uint8)


ANIMALS = {
    "horse": horse,
    "lion": lion,
    "wolf": wolf,
    "deer": deer,
    "elephant": elephant,
    "eagle": eagle,
    "logo": logo,
    "irregular": irregular,
}


def trex() -> np.ndarray:
    """Bipedal dinosaur: big skull with jaw, short arms, two strong legs, long tapering tail."""
    m = _canvas()
    _ell(m, (520, 330), (150, 85), -12)  # torso
    _poly(m, [(640, 290), (700, 200), (745, 215), (690, 330)])  # neck
    _poly(m, [(690, 150), (850, 150), (880, 185), (860, 215), (760, 220), (700, 230), (680, 190)])  # skull
    _poly(m, [(720, 225), (850, 225), (835, 255), (730, 260)])  # lower jaw
    _poly(m, [(640, 360), (690, 400), (680, 410), (630, 380)])  # arm
    _poly(m, [(380, 300), (200, 330), (60, 380), (40, 395), (200, 360), (390, 360)])  # tail
    for x0, x1 in ((470, 450), (540, 560)):  # legs (thigh, shin, foot)
        _poly(m, [(x0 - 45, 350), (x0 + 45, 350), (x0 + 30, 520), (x0 - 20, 520)])
        _poly(m, [(x0 - 15, 510), (x0 + 25, 510), (x1 + 15, 690), (x1 - 15, 690)])
        _poly(m, [(x1 - 30, 680), (x1 + 50, 680), (x1 + 55, 700), (x1 - 35, 700)])
    return m


ANIMALS["trex"] = trex


def fish() -> np.ndarray:
    """Side view of a fish: spindle body, head with eye-less snout, dorsal and ventral fins, forked tail."""
    m = _canvas()
    _ell(m, (500, 390), (290, 120))  # body
    _poly(m, [(240, 390), (110, 270), (150, 390), (110, 510)])  # forked tail
    _poly(m, [(420, 280), (520, 170), (600, 285)])  # dorsal fin
    _poly(m, [(470, 500), (520, 580), (570, 500)])  # ventral fin
    _ell(m, (790, 400), (40, 70))  # head
    return m


def thin_legs() -> np.ndarray:
    """Quadruped with very thin legs (stick-like), to exercise thin-feature handling."""
    m = _canvas()
    _ell(m, (500, 330), (230, 90))  # body
    _poly(m, [(680, 300), (760, 190), (800, 215), (730, 340)])  # neck
    _ell(m, (810, 195), (55, 35), 20)  # head
    for x in (340, 380, 620, 660):
        cv2.line(m, (x, 380), (x + 5, 700), 255, 7)  # stick legs
    _poly(m, [(275, 300), (190, 340), (200, 355), (280, 330)])  # tail
    return m


ANIMALS["fish"] = fish
ANIMALS["thin_legs"] = thin_legs
