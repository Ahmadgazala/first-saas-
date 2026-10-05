# Known issues

Status as of the last working-tree change (**nothing is committed yet**). "Confirmed" = reproduced with a measurement
that is recorded here; "Hypothesis" = plausible but not verified. Evidence scripts: `scratchpad/`.

## A. Confirmed bugs

### KI-1 `_narrow()` measured the wrong thing for mesh volumes — fixed (uncommitted), tests pass
* **Symptom:** valid slices skipped on AI-3D meshes. gt_dino 17 → 10 pieces (7 → 0 slices, top IoU 0.210 → 0.130); penguin
  21 → 13 (14 → 6 slices; front 0.458 → 0.352, top 0.464 → 0.285); photo_trex_kit AI 3D front 0.564 → 0.510.
* **Cause:** the rule used `spine.half_depth = min(up, down)` around z = 0. For a body off that plane (gt_dino body z −10.5…+2.0)
  the proxy is 1.7–3.4 mm while the real slice is 15 mm wide; penguin 4.8 mm vs 64 mm (`narrow_vs_section.py`, `offcentre.py`).
* **Fix:** mesh volumes use the real section width on the slice plane (`SliceGenerator._mesh_section_width`); estimated volumes
  unchanged. Measured after the fix: gt_dino 17 pieces / 7 slices (top 0.199), penguin 21 / 14 (front 0.469, top 0.475),
  photo_trex_kit AI 3D front 0.572, top 0.536.
* **Regression test:** `test_mesh_narrow_rule_measures_the_real_section_not_the_z0_proxy`,
  `test_estimated_path_narrow_rule_is_unchanged`.

### KI-2 Plate growth in narrow zones leaves slivers — open
* On the penguin AI-3D run, growing the plate after the false narrow detection raised "narrowest feature after slot"
  rejections to 1005 (11 → 6 slices). With KI-1 fixed this no longer triggers there, but the mechanism is unchanged where a
  zone IS narrow. Not yet re-measured on genuinely narrow mesh zones.

### KI-3 Appendage depth used global/torso placement — fixed (uncommitted), tests pass
* **Symptom:** T-Rex arm plates at z = ±24.7 and crossbar ±29.2 against a reference half-depth of 11–15 mm (max 22 mm anywhere):
  100 % of the arm voxels outside the reference body; front IoU 0.660 → 0.616 (arm plates −0.015, crossbar −0.027).
  Lion (appendage at the rear, reference half-depth 5.5 mm): plates at ±26.4.
* **Fix:** `_girdle_depth` clamps appendage groups to `max(z_min, local half-depth + T/2)` (`_appendage_half_extent`, estimated
  depth field or mesh `z_intervals`). After: T-Rex plates ±13.1 (one plate-clash step above the floor 9.1), crossbar ±17.6,
  front IoU 0.668, top 0.670 (multi-start median front 0.669 with arms on vs 0.669 off).
* **Regression test:** `test_appendage_plates_and_crossbar_stay_inside_the_local_body`, `test_appendage_half_extent_is_measured_where_the_body_is`.

### KI-4 The slice sequence is order-sensitive — open (test added)
* Shifting only the start of the slice sequence by ±1.5–3 mm moves top IoU a lot at one start position:
  cat_snow top 0.827 (4 starts) vs 0.473 (start 0.0); lion_bw 0.755 vs 0.656. In the cat case the slice positions barely change
  but one 54 mm slice and the tilt pattern differ (tilts 0–37°). The harness's single-start numbers therefore carry ≈ ±0.1
  noise on top IoU (front is stable ±0.01).
* **Cause (partly understood):** greedy forward placement + tilt search: a 2–3 mm lattice shift changes which neighbours
  collide, hence which planes are tilted; the top-view footprint of a tilted slice is large.
* **Test:** `test_slice_sequence_start_does_not_change_the_result_beyond_noise` (5 starts, T-Rex and lion, every run printed,
  per-run floors, median/range reported). It documents the noise; it does not fix it.

### KI-5 Mouth-gap tracing could mistake a head opening for the mouth — fixed (uncommitted), tests pass
* **Cause:** `_traced_mouth` took the largest gap between material stretches at each head position; at two positions the
  traced skull window (7.1 / 6.9 mm) beat the mouth (3.8 / 4.2 mm), the cut path jumped through the window and the hole was
  destroyed (interior count 1 → 0) **before** spacer placement (`hole_trace.py`, `head_spacer.py`).
* **Fix:** scan on the head with holes filled; the straight seam behind the mouth is lowered clear of holes, otherwise the
  generated mouth is used; a lost hole rejects the traced cut. After: both skull plates keep the window, mouth traced, both
  skull spacers placed on the first attempt.
* **Regression test:** `test_mouth_scan_does_not_mistake_a_head_opening_for_the_mouth`, `test_head_opening_and_mouth_are_both_kept_in_the_skull_plates`.

### KI-6 Egg-crate / sheet limits — open
* Egg-crate on AI meshes fails `TOO_MANY_PIECES` on several cases (deer, thin_legs, gt_bird, gt_dino), even after the harness retries at 16 pieces.
* Slice style: elephant, circle and rectangle fixtures do not fit a 600 × 400 mm sheet at 250 mm (huge solid slices); the retry
  loop reduces the piece cap but cannot make a slice smaller.

## B. Limitations (confirmed, by design or by input quality)

* **Eye:** generation receives only the mask. In the reference kit the eye is a shaded raised oval, not a hole; the only
  see-through head opening is the triangular window (kept). Detecting an eye needs the photo's pixels inside the head, a
  closed-contour/shading detector, and a decision whether to cut something that is not a hole physically (`eye_info.py`).
* **AI 3D (TripoSR) from one photo:** smooths detail, infers the hidden side, can be off-centre in z and thin (the AI body of the
  reference photo has no arms/jaw detail and a thin tail). On synthetic flat-colour renders (gt_dino / gt_bird) following the
  reconstruction faithfully moves the kit *away* from the ground truth.
* **Planar skeleton:** backbone/tail/skull are flat plates at fixed depths; a body that leaves the z = 0 plane (three-quarter
  view) is followed by the slices but not by the backbone.
* **Thin tail/neck:** need Object depth ≳ ×1.6 to get slices (a slot needs ≥ 8.6 mm of body at T = 3); otherwise the plate covers it.
* **Arms/appendages:** small (< 0.15 % of the area) or very thin parts are ignored; at most 2 appendage clusters.
* **Harness:** the 10 real photos are not in the repository (uploaded ones under `backend/data/uploads/`, the others in the
  git-ignored `backend/fidelity/photos/` or the folder named by `PUZZLECUT_FIDELITY_PHOTOS`); cases whose photo is missing are skipped; timings are
  meaningless if the laptop sleeps; GrabCut is not bit-reproducible.

## C. Hypotheses (not verified)

* The lion_bw "appendage" at the rear (bbox x −125…−63, y 0…38) is the tail tuft — inferred from its bounding box, not
  viewed.
* The remaining lion_bw top-IoU gap vs the pre-appendage state (≈ −0.03 typical) comes from the appendage plates sitting at the
  `z_min` floor (9.1 mm) beyond a 5.5 mm body; a lower floor for thin appendages might help.
* Penguin AI 3D estimated-vs-AI differences are mostly reconstruction artefacts (two shells along the spine point at s = 92.9).
* A single "max slices" cap may be a poor control compared with the Detail setting alone.

## D. Verification status of the latest working tree

* Backend: `tests/test_slice.py` 42 passed; the rest of the suite 349 passed (no failures) — 391 tests, AI models off.
  Frontend: 56 passed on the previous run (no frontend files changed since).
* Harness cases re-measured after the three fixes (numbers in KI-1 / KI-3 / KI-5). The full 24-case slice harness has **not**
  been re-run on the final tree (see `docs/roadmap.md` §0).
