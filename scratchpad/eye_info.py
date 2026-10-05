import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))  # <repo>/backend, wherever the repo lives
import numpy as np, cv2
from pathlib import Path
import app.vision.segmentation as S
from app.vision.image_io import load_image, resize_max_side

data = Path("trex_ref.png").read_bytes()
rgba = resize_max_side(load_image(data).rgba, S.WORKING_MAX_SIDE)
h, w = rgba.shape[:2]
print("working size", w, h)
rgb = rgba[..., :3]
bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR); gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
ring = max(2, round(min(h, w) * 0.02))
dist, hom = S.background_distance(lab, ring)

def holes(m, label):
    b = (m > 127).astype(np.uint8)
    cs, hr = cv2.findContours(b, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    hs = []
    for i, c in enumerate(cs):
        if hr[0][i][3] >= 0:
            M = cv2.moments(c); a = cv2.contourArea(c)
            if a >= 2: hs.append((int(a), (int(M["m10"] / max(M["m00"], 1e-9)), int(M["m01"] / max(M["m00"], 1e-9)))))
    obj = int(b.sum())
    print(f"{label:34s} object {obj:6d}px  holes {len(hs):3d}  >=0.1% of object: {[x for x in hs if x[0] >= 0.001 * obj]}  (filled by old 2% rule: {[x for x in hs if x[0] <= 0.02 * obj][:6]})")
    return hs

ai = S._ai_candidate(rgba)
holes(ai, "raw IS-Net mask")
c_ai = S.clean_mask(ai)
holes(c_ai, "after clean_mask (current, 0.1% fill)")
m_old = S.fill_holes(S.select_main_component(cv2.morphologyEx(cv2.morphologyEx(ai, cv2.MORPH_CLOSE, S._kernel(max(3, round(min(h, w) * 0.01)))), cv2.MORPH_OPEN, S._kernel(max(3, round(min(h, w) * 0.01))))), 0.02)
holes(m_old, "old clean_mask (2% fill)")
bg = S._background_candidate(dist)
holes(bg, "raw colour-model (background) mask")
holes(S.clean_mask(bg), "colour-model after clean_mask")
final_mask = c_ai
# dark enclosed regions inside the object in the photo itself
for thr in (35, 55, 80):
    dark = ((gray < thr) & (final_mask > 127)).astype(np.uint8)
    n, lab_, stats, cent = cv2.connectedComponentsWithStats(dark, connectivity=8)
    comps = sorted([(int(stats[i, 4]), (int(cent[i][0]), int(cent[i][1]))) for i in range(1, n) if stats[i, 4] >= 15], reverse=True)[:8]
    print(f"dark (gray<{thr}) components inside the mask (>=15px): {comps}")
# annotated crop of the head
ys, xs = np.nonzero(final_mask > 127)
head = (slice(30, 230), slice(40, 360))
vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
cs, _ = cv2.findContours((final_mask > 127).astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
cv2.drawContours(vis, cs, -1, (0, 200, 0), 1)
dark = ((gray < 55) & (final_mask > 127)).astype(np.uint8)
n, lab_, stats, cent = cv2.connectedComponentsWithStats(dark, connectivity=8)
for i in range(1, n):
    if stats[i, 4] >= 15: cv2.circle(vis, (int(cent[i][0]), int(cent[i][1])), 3, (0, 255, 255), -1)
crop = vis[head]; crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
cv2.imwrite("eye_head_crop.png", crop)
cv2.imwrite("eye_head_plain.png", cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)[head], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
print("saved crops")
