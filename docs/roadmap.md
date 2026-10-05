# Roadmap

Prioritised. Each item names its evidence and how success is measured; nothing is done without a measurement
(see rules in `CLAUDE.md`).

## 0. Now — close the current work (before anything else)

1. Re-run the complete backend suite and the frontend checks on the final working tree; record the numbers.
2. Re-run the fidelity harness for the slice style (`--styles slice`) and compare with `backend/fidelity/reports/slice_before`
   and `slice_after` (`python -m fidelity.compare`). Success: no individual metric worse beyond the multi-start noise
   (front ±0.01, top ±0.1); gt_dino / penguin AI-3D back at their pre-regression piece counts.
3. First restructuring commit (see "Repository organisation").

## 1. Geometry quality (in this order)

1. **Order sensitivity of slice placement** (KI-4): make the result less dependent on the lattice start (e.g. evaluate a
   few starts and keep the best valid one, or place slices from the thickest section outwards). Success: top-IoU spread across
   starts < 0.05 on T-Rex, cat, lion.
2. **Plate growth slivers** (KI-2): cap the growth so a slot cannot leave a feature < min size; success: no extra rejections
   on genuinely narrow mesh zones.
3. **Appendage depth floor** (hypothesis in known-issues C): allow thin appendages closer than `z_min` if the spacer geometry
   allows; success: lion_bw top gap closed without front loss.
4. **Neck slices** when a skull is detected right after a short neck; **thin tail** usable without Object depth ×1.6
   (slice width vs slot) — only with measured benefit.
5. Skull/limb depth from head/limb sections (today: depth field + side outline).
6. Backbone/tail that follow a body leaving the z = 0 plane (rotate the mesh so torso section centres lie on z = 0, or offset
   backbone segments) — measure the lion photo first (section centres −19.5 → +24.4 mm).

## 2. Inputs and detection

* **Eye / raised features**: needs the photo inside the head (pass image-derived "features" next to the mask), a
  closed-contour detector and an explicit policy (engrave vs cut). Do not start before that design is agreed.
* Traced head windows next to spacer slots: keep the opening when a spacer must pass near it (currently spacers move away,
  default stations are the fallback).
* Segmentation: reproducible GrabCut; decide whether non-head holes should become openings in other plates.

## 3. Product and quality of life

* "Adjust the guessed 3D" step: depth/thickness controls with a live 3D preview before cutting (depth slider exists, no live preview).
* Piece-count semantics for slice style ("max pieces") and sheet-fit feedback for huge solid shapes (circle, elephant).
* Optional cutting-service preflight (e.g. unit/closed-contour checks against a vendor's published limits) — only if ordering
  from a service becomes a goal; PuzzleCut already validates its own DXF/SVG.

## 4. Engineering hygiene

* Commit real regression photos or small masks (with permission) so the harness is reproducible from a clone; today only
  `backend/tests/data/trex_photo_mask.png` is in the repository.
* CI on GitHub: backend `pytest` (AI off) + frontend `npm test`, `typecheck`, `lint` on every push.
* Promote useful `scratchpad/` scripts into `backend/fidelity/` with tests (ablation and multi-start are the candidates).
* Keep `PUZZLECUT_CONTEXT.md` short or fold it into these docs after the first commit.

## Repository organisation (proposed — nothing moved yet)

The project is a monorepo `backend/` + `frontend/`, so the generic `app/ src/ tests/` layout does not apply; keep it and add:

| Proposal | Why | Risk |
|---|---|---|
| keep `backend/`, `frontend/` as they are | working, documented, Docker build contexts depend on them | none |
| new `docs/` (done), `scratchpad/` (done, copies only), `CLAUDE.md` (done) | persistent context for agents | none |
| `lion/` → `experiments/lion/` | hand-coded lion prototype (own venv, not used by the app) | `lion/.venv` is git-ignored; move after the first commit |
| `3d-printing-video/` + `3d-printing-explainer-script.md` → outside the repo or `experiments/video/` | unrelated to PuzzleCut; the 112 MB `.mp4` and 7 MB `.wav` exceed GitHub's limit (now ignored) | none if sources stay |
| `PUZZLECUT_CONTEXT.md` → `docs/session-log.md` | long session log with measured numbers | links in `CLAUDE.md` / `README.md` need updating |
| `.claude/launch.json` → portable (relative) version | contains absolute paths of this machine (now ignored) | none |
| `scripts/` (does not exist) | add only when a real script exists (e.g. `scripts/dev.sh`) | none |
| `examples/` (does not exist) | candidates: `lion/lion_puzzle.svg/.dxf`, `frontend/public/showcase/` | none |
