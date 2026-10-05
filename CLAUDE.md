# CLAUDE.md — PuzzleCut

Read this first. Details live in `docs/`; the older, very detailed project notes are in `PUZZLECUT_CONTEXT.md`
(session log with measured numbers) and `docs/legacy-readme.md` (the original long README).

## 1. What this project is

**PuzzleCut** turns **one photo (or one 3D model: STL / OBJ / GLB / PLY)** into a **laser-cut, slot-together 3D kit**:

`image / reference → silhouette → 3D body (AI reconstruction, estimate, or uploaded mesh) → structural kit
→ half-lap slots → validation → kerf compensation → nesting on one sheet → SVG / DXF / PDF + assembly guide`.

Output is real vector geometry in millimetres: layer `CUT` (kerf-compensated tool paths) and `ENGRAVE` (labels, never
cut). Target physical look: a body-shaped spine plate + dense solid cross-section slices + silhouette limb plates
(see the photographed T-Rex kit used as the reference). Generic engine: **no per-animal templates, ever.**

Repository state: GitHub `Ahmadgazala/first-saas-` (public, empty until the first restructuring commit). The local
folder was NOT a git repository when this file was written.

## 2. Architecture (one screen)

| Part | Where | Notes |
|---|---|---|
| Frontend | `frontend/` | Next.js 16 + React 19 + Three.js + Tailwind 4. `src/components/CreateWorkspace.tsx` (workflow), `SettingsPanel.tsx`, `SkeletonEditor.tsx`, `PuzzleViewer.tsx` (3D), `src/lib/{api,settings,types}.ts` |
| Backend API | `backend/app/api/`, `app/main.py` | FastAPI; jobs: create → upload → (silhouette/analyze) → generate → download. `app/services/job_service.py`, `pipeline.py` |
| Vision | `backend/app/vision/` | segmentation (rembg IS-Net + classical candidates), suitability, image IO |
| Reconstruction | `backend/app/reconstruction/`, `third_party/TripoSR` | local TripoSR (MIT) photo → mesh; gated by alignment IoU ≥ 0.55 and depth ratio ≥ 0.14 |
| Volume / mesh | `backend/app/geometry/volume.py` | `DepthVolume` (estimate) and `MeshVolume` (exact plane sections); `prepare_mesh`, `remesh`, `scale_depth` |
| Structure | `backend/app/geometry/anatomy.py`, `kit/structure.py` | torso/appendages, spine curve, head/neck/tail, limbs, thin appendages, neck-constriction head |
| Slicing | `backend/app/geometry/kit/slice.py` (default style), `kit/builder.py` (skeleton kit), `engine.py` (egg-crate) | `SliceGenerator(KitGenerator)` |
| Hole detection | `vision/segmentation.py`, `geometry/normalize.py` | segmentation keeps holes ≥ 0.1 % of the object; `fill_small_holes` restores the old behaviour for non-slice styles; slice cuts holes inside the head from the skull plates |
| Mouth tracing | `kit/builder.py::_traced_mouth` | open mouth of the outline → skull + jaw plates; teeth come from the outline |
| Appendage placement | `kit/builder.py::_girdle_depth`, `_appendage_half_extent`, `kit/structure.py::_thin_appendage_limbs` | arms/claws/small fins; clamped to local body half-depth |
| Slots / kerf | `geometry/slots.py` | slot width = thickness + clearance; tool path = outline offset outwards by kerf/2 |
| Nesting | `geometry/nesting.py` | deterministic raster bottom-left fill, 0/90/180/270°, exact verification |
| Validation | `validators/geometry_validator.py` | nothing is exported unless it passes (closed/simple outlines, walls, slots, 3D interpenetration, connectors, connectivity, sheet bounds, spacing) |
| Export | `exporters/` (`svg.py`, `dxf.py`, `pdf.py`, `assembly_guide.py`, `sheet.py`, `render.py`) | one shared flattened sheet geometry feeds every format |
| Measurement | `backend/fidelity/` | standalone harness: runs production code, measures IoU / coverage / Chamfer vs references |

Styles (`Style` enum): `slice` (default, "Solid slice"), `kit` (skeleton kit), `skeleton`, `solid` (egg-crate).
Canonical frame (mm): x along the body, y up, z depth, spine plate at z = 0. Cross pieces use `RibFrame(cx, cy, dx, dy)`,
local (u = z, t).

## 3. Engineering rules (non-negotiable)

1. **Do not invent geometry without evidence** (no generated eye/nostril/window unless the outline has an opening there;
   no species templates).
2. **Preserve valid segmentation holes.** Never destroy an existing opening (a traced head window must survive the
   mouth cut and spacer placement).
3. **No large architectural rewrites without evidence.** Smallest change that fixes the measured root cause.
4. **Prefer measured geometry over heuristics** (real section width over a z = 0 proxy; local body depth over a global one).
5. **Run the regression tests after every geometry change** (see §6) and keep every individual result visible: the
   slice sequence is order-sensitive, a median must never hide a bad run.
6. **Never claim an improvement without measurements.** Report before/after numbers; say what was not verified.
7. Manufacturing validity beats looks: a pretty preview with invalid geometry is a failure.
8. Public repository: never commit secrets, user uploads (`backend/data/`), real photos, model weights or files > 100 MB.
9. Do not install external skills or add paid-API dependencies without asking.

## 4. Known issues (details and evidence: `docs/known-issues.md`)

Confirmed by measurement; status as of the last working-tree change (uncommitted):

* **`_narrow()` used the wrong proxy for meshes** (spine half-depth around z = 0 instead of the real section width) →
  valid slices skipped (gt_dino 17 → 10 pieces, penguin 21 → 13). *Fixed in the working tree; tests pass.*
* **Appendage depth used global/torso placement** (arms and crossbar outside the body; T-Rex front IoU −0.043).
  *Fixed in the working tree (clamp to local half-depth + margin); tests pass.*
* **Mouth-gap tracing could mistake an existing head opening for the mouth** and destroy it inside `_traced_mouth`,
  before spacer placement. *Fixed in the working tree (scan on the head with holes filled; seam kept clear of holes); tests pass.*
* **Slicing is order-sensitive**: shifting only the start of the slice sequence by a few mm moves top IoU by up to
  ~0.1 (cat 0.473 vs ~0.826; lion 0.656 vs ~0.755). Open — multi-start regression test added, root cause (greedy
  placement + tilt search) not fixed.
* **Eye detection lacks image information at the geometry stage**: generation only receives the mask; in the reference
  kit the eye is a shaded raised oval, not a hole (the only see-through head opening is the triangular window).
* Plate growth in genuinely narrow zones can leave slivers (1005 rejections "narrowest feature" on the penguin run).
* Egg-crate on AI meshes still fails `TOO_MANY_PIECES` on several cases; elephant/circle/rectangle do not fit the
  600 × 400 sheet in slice style.

## 5. Current priority

1. Fix mesh `_narrow()`  — *done (uncommitted)*
2. Fix appendage local depth — *done (uncommitted)*
3. Fix mouth-gap detection — *done (uncommitted)*
4. Improve multi-start regression testing — *added (`test_slice_sequence_start_does_not_change_the_result_beyond_noise`)*
5. Only then consider further geometry improvements (see `docs/roadmap.md`).

Backend suite on the final tree: 391 passed (42 slice + 349 others). Next concrete step: re-run the fidelity harness for the slice style
(`--styles slice`) and compare with `backend/fidelity/reports/slice_before` / `slice_after`.

## 6. Run and test

```bash
# backend (Python 3.12), from backend/
python -m venv .venv && .venv/bin/pip install -e ".[dev]"      # add ".[ai]" for rembg + TripoSR (large)
.venv/bin/uvicorn app.main:create_app --factory --port 8000
.venv/bin/python -m pytest                                      # ~390 tests, AI models off (-m 'not ai')
.venv/bin/python -m pytest tests/test_slice.py -o addopts=""    # solid-slice style (~10 min)
.venv/bin/python -m pytest tests/test_mesh_usage.py -o addopts="" # the mesh must drive the kit

# frontend, from frontend/
npm install && npm run dev            # http://localhost:3000
npm test && npm run typecheck && npm run lint && npm run build  # 56 tests

# fidelity harness (measurement only), from backend/
.venv/bin/python -m fidelity.run --styles slice --out fidelity/reports/<name>   # ~20-30 min, keep the laptop awake
.venv/bin/python -m fidelity.report --dir fidelity/reports/<name>
.venv/bin/python -m fidelity.compare fidelity/reports/slice_before fidelity/reports/<name>
.venv/bin/python -m fidelity.trace --gt quadruped --out /tmp/t          # one input, every stage (or --image / --model)
.venv/bin/python -m fidelity.ab_kit                                     # mesh-driven vs depth-only kit
```

Regression data:
* `backend/tests/animals.py` — drawn side-view masks (horse, lion, wolf, deer, elephant, eagle, fish, logo, …).
* `backend/tests/meshes.py` — procedural meshes: quadruped, biped, bird (exact 3D ground truth).
* `backend/tests/data/trex_photo_mask.png` — silhouette of the photographed T-Rex kit (diagonal body, big head, raised
  foot, open mouth): the reference pose of the slice style.
* `backend/fidelity/cases.py` — 24 harness cases; the 10 real photos live under `backend/data/uploads/` or the git-ignored
  `backend/fidelity/photos/` (override: `PUZZLECUT_FIDELITY_PHOTOS`) and are NOT in the repository; reconstructions are cached
  in the git-ignored `backend/fidelity/cache/`. `lion/grid.py` / `overlay.py` read `lion/reference.jpg` (git-ignored; override:
  `LION_REFERENCE_IMAGE`).
* `scratchpad/` — historical analysis scripts (ablation, phase noise, appendage cost, head/spacer, eye). Not production.

Known test-infrastructure limits: harness timings are meaningless if the laptop sleeps (lid closed); GrabCut
segmentation is not bit-reproducible between runs.

## 7. Conventions

* Python: match surrounding style; comments explain WHY (measured evidence), docstrings state the contract.
* New geometry behaviour gets a test that fails if the behaviour is removed (see `tests/test_mesh_usage.py`, `tests/test_slice.py`).
* Docs: update `docs/known-issues.md` (confirmed vs hypothesis) and `docs/roadmap.md` at the end of a session.
* Do not move or delete files without asking; propose restructuring first (`docs/roadmap.md`, "Repository organisation").
