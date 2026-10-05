# PUZZLECUT_CONTEXT.md — working context (source of truth between sessions)

Last updated: 2026-10-04 (session: "fix the pipeline so the kit generator actually uses the 3D mesh").
Update this file at the end of every major development session. Numbers here are measured, never estimated;
each one names the command that produced it.

---

## 1. Purpose

PuzzleCut AI turns **one image** (photo, drawing) or **one 3D model** (STL / OBJ / GLB / PLY) into a
**laser-cut, slot-together kit** of flat pieces: SVG / DXF / PDF cut sheets with kerf-compensated cut paths and
engraved part labels, nested on one sheet, plus a 3D preview and an assembly guide.

Styles (`Style` enum; `Kit Style ├ Solid Slice (default) ├ Skeleton kit ├ egg-crate ...`):
- **slice** (default, `app/geometry/kit/slice.py`, `SliceGenerator(KitGenerator)`): solid-slice 3D puzzle. A
  body-shaped spine plate (z = 0), dense **solid** cross-section slices (belly to top of the back, torso, neck and
  tail alike, tilted so neighbours never collide), limb plates cut from the limb's own outline in the image,
  skull plates with openings/mouth/teeth. Target look: the photographed T-Rex kit (body plate + many solid slices).
- **kit** (skeleton kit, `app/geometry/kit/builder.py`): backbone bar, thin arch ribs, vertebrae, skull + jaw,
  designed limb bones, bowtie connectors. Kept; not the default.
- **solid** / **skeleton** (egg-crate, `app/geometry/engine.py`): profile plates + cross ribs cut from the body volume.
All generic: driven by the detected structure and the 3D body, never by species templates (no `if animal == ...`).

**User controls (slice style):** Object depth (`depth_scale`, 0.3–3): multiplies the estimated depth field (photo
only) or the mesh z (AI 3D / uploaded model); it changes slice widths, skull depth, limb depth and slot positions
(tested). Detail (= Complexity): slice pitch. Max pieces (`piece_count`) caps the total; `rib_count` caps the slices.

Not production-ready in the general sense: geometry is validated for manufacturability on every job (see §8), but
visual fidelity to the subject is limited and measured (§9).

## 2. Architecture

| Layer | Tech | Where |
|---|---|---|
| API + jobs | FastAPI (Python 3.12), SQLAlchemy (Postgres; SQLite in tests), background task queue | `backend/app/api`, `backend/app/services`, `backend/app/models` |
| Vision | OpenCV, scikit-image, rembg IS-Net (`isnet-general-use`), GrabCut, Otsu, Canny | `backend/app/vision` |
| AI 3D | TripoSR (MIT, local, MPS/CPU, ~6-24 s), vendored | `backend/app/reconstruction`, `backend/third_party/TripoSR` |
| Geometry | Shapely 2.1, trimesh 5.1, numpy/scipy | `backend/app/geometry` |
| Export | ezdxf, reportlab, SVG writer | `backend/app/exporters` |
| Frontend | Next.js 16, React 19, Three.js | `frontend/src` |
| Measurement | fidelity harness (standalone) | `backend/fidelity` |

No paid API anywhere. `lion/` at the repo root is an unrelated hand-coded prototype (not used by the app).

### Canonical frame and piece types
- mm; **x along the body, y up, z depth**; spine plane z = 0.
- **Profile plates** at z = const: local (u, v) = (x, y).
- **Cross pieces** (ribs, vertebrae, spacers) on a `RibFrame(cx, cy, dx, dy)`: plane containing the z axis through
  (cx, cy) with in-plane direction (dx, dy); local (u = z, t). World = (cx, cy, 0) + u·(0,0,1) + t·(dx, dy, 0).
- `Volume` (`app/geometry/volume.py`): `DepthVolume` (estimated: silhouette + symmetric half-depth field) and
  `MeshVolume` (exact plane sections of a real mesh; `_slab` = intersection of the sections at ±T/2 so a piece never
  pokes out of the body). Both expose `silhouette`, `depth`, `profile_section`, `cross_section(frame, a, b, T)`,
  `z_intervals(x, y)`.

## 3. Data flow (traced; `python -m fidelity.trace` prints every stage)

```
image ─► load/validate ─► segmentation (rembg IS-Net vs classical, best score) ─► mask
      ─► suitability (blocking: no subject / several / cut off)
      ─► [shape_source auto|ai_3d] TripoSR ─► remesh (voxel 140, gaussian, taubin, ≤60k faces)
            ─► align_to_silhouette (24 axis rotations, IoU with the mask) ─► prepare_mesh (scale to size,
               rest on y=0, centre x, z centre of mass at 0)
            ─► gates: alignment IoU ≥ 0.55 and depth ratio ≥ 0.14, else fall back to the estimate
      ─► [3D upload] remesh ─► orient by file convention + longest horizontal axis ─► prepare_mesh
      ─► MeshVolume(mesh)  (or normalize_silhouette + depth model for the estimated path)
      ─► style kit:   StructureAnalyzer(silhouette, depth) ─► AnimalStructure (spine curve, head/neck/torso/tail,
                      limbs with joints) ─► KitGenerator.build(structure, volume) ─► PuzzleModel
         style solid: PuzzleGenerator.generate_from_mesh / generate (egg-crate)
      ─► validate_model ─► compensate_kerf (tool paths) ─► validate_toolpaths ─► kit frame ─► nest ─► validate_layout
      ─► SVG / DXF / PDF / preview / assembly guide / metadata
```

### Where the 3D mesh influences the kit (after this session)
| Kit element | Driven by | Before this session |
|---|---|---|
| spine curve, head/torso/tail ranges, limb joints | the mesh's side projection (`volume.silhouette`) | same |
| backbone height, skull depth `zs`, vertebra wing | `volume.depth` = mesh half-thickness around z = 0 (min(up, down)) | same |
| **ribs** | **`volume.cross_section` on each rib's own plane** → `BoneLibrary.section_rib` (template only as a clipped fallback) | template arch, width = 0.92 × scalar half-depth, capped by one global limb depth |
| **rib-plan widths** | measured section widths (25 stations, interpolated) | 0.92 × half-depth |
| **limb plate depth** | per limb group **and per side** (+z / −z): the **measured depth of the legs** in the mesh (`z_intervals`, `limb_mode = "measured"`); ribs near the girdle are narrowed to fit between the plates. Falls back to just outside the measured rib cage when no leg is found on that side (`"cage"`) | one global ±zL from the widest rib anywhere |
| **girdle crossbars** | span from the −z plate to the +z plate (asymmetric when the body is) | always symmetric to the deepest plate |
| **vertebrae** | processes clipped to the measured section | not clipped (could leave the body) |
| skull plates, jaw, limb bone outlines, backbone | side outline + depth (unchanged) | same |

`model.analysis["kit_volume"]` records, per job: volume kind, rib sources (`mesh-section` / `template`), every
rib's section width / t-range / area / u-centre / u-scale (or `fallback_reason` for a template rib), and every
limb group's chosen depth per side with the cage width, measured leg spread and the old global estimate.

## 3b. Solid-slice style (2026-10-04)

Pipeline: single image → side profile → estimated depth (or AI 3D mesh) → structure (spine, head, limbs) → spine plate
→ solid slices → half-lap slots → validation → kerf → nesting → SVG/DXF.

- **Spine plate**: `SliceGenerator._hb` = 28 % of the local body height per side (kit bar: 16 %, min bar height), via the
  inherited `_backbone` (segments + bowties when longer than the sheet allows).
- **Slices** (`_ribs_and_vertebrae`, `_try_slice`, `_slice_section`, `bones.slice_plate`): section of the volume
  (`MeshVolume` exact, else `DepthVolume`) on the plane perpendicular to the spine, clipped belly (limb-free outline)
  to top of the back, internal cavities filled, features < min removed. Plane directions tried: spine normal, parallel
  to the previous slice, ±10…45° (a curved spine makes perpendicular slices converge on the inside). Width narrowed
  (0.85, 0.7) when a limb/skull plate is in the way. Accepted only through `_try_cross` (same checks as the validator).
- **Count/spacing** (`_gap_profile`): pitch = (slot width + wall + 2 mm) × Detail factor (2.6 / 1.8 / 1.15), up to 40 %
  denser where height × width of the section changes quickly, never below 1.05 × the physical minimum; stretched if
  more slices than the cap are needed. Each slice is placed one gap beyond the previous *placed* slice.
- **Limbs**: plate = the limb's silhouette polygon (Chaikin-smoothed, opened by half the min feature) ∪ a blade around
  the girdle strut; one plate per limb (no bowtie seams).
- **Structure fixes this needed** (generic, found on the reference T-Rex photo): (1) legs may stop up to 22 % of the
  height above the lowest line (a raised foot used to leave < 2 legs → "not an animal" → no head/tail at all);
  (2) a head merged into the torso blob is found as the bulge beyond the neck constriction of the width profile
  (`_neck_constriction`), and an open jaw is then part of the head, not a limb.
- **Cross-piece check fix**: slot-to-slot wall distance is measured on the part of the channel inside the plate.
- **Frontend**: Solid slice is the default style; "Object depth" slider (real effect, tested); the STL hint shows only
  for the skeleton kit; Skeleton editor tab works for both kit styles.
- **Sheet fit**: the kit retry loop reduces the piece cap by 30 % up to 8 times for slices before reporting
  `PIECES_DO_NOT_FIT` (circle/rectangle/elephant fixtures still do not fit 600×400 at 250 mm: huge solid slices).

Tests: backend 376 passed (incl. `tests/test_slice.py`, 27 tests: structure of the reference pose, solid slices,
count follows Detail / length / cap, depth control on estimate and mesh, different meshes → different slices/spine/parts/
slots/SVG, slots = T + clearance, kerf offset, sheet bounds, CUT vs ENGRAVE, 4 other objects incl. a logo and a fish),
frontend 56 passed. End to end through the HTTP API with the T-Rex photo and defaults: job completes, style `slice`,
`depth_scale` 1.3 stored, SVG/DXF/PDF/assembly guide produced. With AI 3D (TripoSR) on that photo the body is a thin
reconstruction: 19 pieces, 8 torso slices and no tail slices (thin tail mesh) - reconstruction quality is the limit there.

Measured on the reference photo mask (`tests/data/trex_photo_mask.png`, default settings): 26–28 pieces, 16–18 slices,
skull (+jaw), 2 leg plates, validation OK. Known gaps vs the reference kit: no arms (small limbs < 3.5 % of the area
are ignored), the thin estimated tail gets few slices unless Object depth is raised (≥ 3 tail slices at ×1.6), no neck
slices when a skull is detected right after a short neck, skull openings are procedural (eye/nose circles + generated
mouth), not traced from the photo.

### Solid slice: thin appendages, traced head, narrow regions (2026-10-04, later session)
- **Arms / small appendages** (`structure._thin_appendage_limbs`, only with `StructureAnalyzer(thin_appendages=True)`,
  which `SliceGenerator` sets): EXTREMITY appendages of 0.15–3.5 % of the area, ≥ 4 mm thick, ≥ 2.5 × min-feature long,
  not pointing dorsally, not on the head or an existing limb; fragments within 7 % of the axis length are merged
  (closing 4 mm) into one plate outline, at most 2 clusters. The skeleton kit is unchanged.
- **Head traced from the outline** (`builder._traced_mouth`, `trace_head`): the open mouth is where a cross line meets
  two separate stretches of material with a gap; the head is cut along the gap midline to the snout and straight back
  from the apex (seam lowered below the backbone only if no skull-spacer station would find upper-plate material at
  the spine). Teeth stay because the head outline is no longer Chaikin-smoothed. If tracing leaves the skull
  unattachable, the generated mouth is used (warning). `invent_openings=False`: no eye/nostril/window unless the
  outline has an opening there.
- **Openings**: segmentation now keeps see-through holes ≥ 0.1 % of the object (was: filled below 2 %);
  `normalize.fill_small_holes` restores the old behaviour for egg-crate and skeleton kit. Slice cuts holes inside the head
  out of the skull plates (a skull spacer slot may open one into a notch: spacers first move away, default stations
  are the fallback). The reference photo's mask has 4 holes; 1 lies in the head.
- **Narrow regions** (`SliceGenerator._narrow/_hb`): where 2 × half-depth < slot + 2 × min-feature + 0.5 (≈ 8.6 mm) no
  slice is attempted (no slot is forced) and the spine plate grows to ~92 % of the local height (smooth 8 mm ramp).
  Measured silhouette gain on the reference pose (side IoU of the assembled kit): +0.003 at depth ×1.0, +0.008 at ×0.6,
  +0.023 at ×0.4. Cost: less top-view footprint where slices are skipped (see below).
- **Fidelity (slice style only, harness `--styles slice`)**: `fidelity/reports/slice_before` → `slice_after`;
  `python -m fidelity.compare fidelity/reports/slice_before fidelity/reports/slice_after`. Side IoU improved on 21 of 45
  comparable mode-cases (worse on 2; mean +0.019, median +0.003); 3D front/top IoU is mixed (front 10 better / 11 worse,
  top 10 / 12). Ablation: `narrow-skip` alone costs photo_cat_snow top IoU 0.826 → 0.473 (that reference is the
  generator's own depth estimate, not ground truth); thin appendages cost photo_trex_kit front IoU 0.660 → 0.616.

## 4. Root cause fixed this session (and how it was found)

1. **The kit never saw the mesh.** `services/pipeline.py` (kit + mesh branch) passed only `volume.silhouette` and
   `volume.depth` to `KitGenerator.generate`; `KitGenerator` had no access to the 3D body, so ribs were template
   arches sized from a scalar half-depth. Measured with `fidelity.trace` on the lion photo (TripoSR mesh): ribs were
   **49 %** of the real section width (median), hull IoU with the section **0.28**, max protrusion out of the body
   **28 mm** for ribs (45 mm for crossbars). Fix: `KitGenerator.generate/build(..., volume=...)`, pipeline passes
   `volume=volume`, ribs cut from sections, limb depths per side, asymmetric crossbars (§3). After: width ratio
   **1.00**, rib hull IoU **0.67**, 10/11 ribs from sections, section ribs never protrude, total material outside
   the body 3069 → 2302 mm².
2. **Stale section cache on copied meshes** (`volume._bodies`). The connected-shell cache lived in
   `mesh.metadata`, which `trimesh.copy()` copies; `engine.py` copies and rotates the mesh for horizontal
   orientation, then sectioned the *unrotated* shells (side IoU 0.061 instead of 0.858; also the unhandled
   `AttributeError: 'NoneType' object has no attribute 'bounds'` crashes in Phase 2). Fix: cache keyed by
   `hash(mesh.vertices)` (trimesh's tracked array hash: equal for a copy, changes on transform, ~0.3 µs).
3. **Limb bone silently lost** (pre-existing, found by the new bird test): when a limb seam could not take a
   bowtie, the two bones were re-merged with `buffer(1e-4)`, but they are split with a 0.02 mm seam gap
   (`_separate`), so `clean()` kept only the larger bone; the bowtie then gripped nothing →
   `INVALID_GEOMETRY: connector C01 does not grip L02` for the bird upload at default settings. Fix: close the seam
   gap (`SEAM_GAP`) when merging and refuse a merge that loses area.

## 5. Decisions (and why)

- **Smallest change, not a redesign.** Ribs (the pieces that define the body's width and depth) now come from
  the mesh; the plate-based skeleton (backbone at z = 0, skull at ±zs, each limb group at one depth per side) is
  unchanged. A z-bent backbone or per-bone limb depths would be a redesign.
- **Limb depth per side, crossbars asymmetric**: a measured body need not be symmetric about the spine plane
  (lion: shoulder section z −42…+6 mm); a symmetric ±zL left the short side's plate and crossbar ~46 mm outside it.
- **Rib shape = band inside the section outline**, open at the belly (gap 30 % of the width, centred on the
  section), axis below the crown always cleared (the backbone slot runs down there; otherwise the slot cut the
  ring in two — that was 38 of 54 rejections on the lion before the fix). Ribs stop at the crown top (the body
  above the backbone is left to the backbone and vertebrae, as in a skeleton).
- **Torso section only**: the section is cut at the side view's belly line computed *without the limbs*
  (`_belly`), so legs hanging below the body are not turned into ribs.
- **Template fallback stays**, but clipped to the section and recorded (`fallback_reason`), so a failure is visible,
  not hidden.
- **Legs at their measured depth (default, 2026-10-04)**, ribs narrowed near the girdle instead of legs pushed outside
  the rib cage: quadruped front IoU 0.455 → 0.580, material outside 0.38 → 0.13, biped Chamfer 8.95 → 4.99 mm, same
  piece counts. `KitGenerator.limb_mode = "cage"` restores the old placement.
- **Estimated path unchanged** (no volume → template ribs, symmetric limbs), to keep the photo-without-3D
  behaviour stable (harness: identical `kit_estimated` / `solid_estimated` numbers on all fixtures). Two bug fixes
  apply to every path: the bone re-merge (§4.3) and the stale section cache (§4.2, egg-crate on meshes).
- **Measurement before change**: the fidelity harness (`backend/fidelity`) is standalone and proven
  output-identical to production (`fidelity/tests/test_harness.py`).

## 6. Solved problems (history, short)

MVP pipeline; segmentation competition + GrabCut guard; local TripoSR; fossil kit with bone library and skeleton
editor; Phase 1 audit; Phase 2 fidelity harness; this session: §4 items 1-3. Earlier fixes worth knowing: GEOS
non-noded crashes guarded in `clean()`; skull/jaw coplanar overlap (`_separate`); knee bowtie margin; hinge bowtie
host tolerance; nesting search broadened; frame kerf spacing; GrabCut hang on blank images.

## 7. Known problems / limitations (measured where possible)

- **Planar axial skeleton.** Backbone and tail vertebrae lie in z = 0. A body that leaves that plane is followed by
  the ribs but not by the backbone: lion photo reconstruction (three-quarter view) has rib section centres drifting
  from z = −19.5 to +24.4 mm along the body; its two tail vertebrae sit 8-11 mm from the mesh's tail and protrude
  ~22-24 mm (trace `max_protrusion_mm`).
- **Limb plates are flat at one depth per side**: legs now stand at their measured depth, but a leg whose depth changes
  along its length (splayed, bent sideways) is approximated by one plane. Ribs next to a girdle are narrower (they
  must fit between the limb plates).
- **Front-view fidelity is dominated by flat plates** (backbone, limbs, spacers seen edge-on): rib changes move
  front IoU only a little on symmetric bodies (quadruped 0.437 → 0.458) and it is slightly *lower* on the slim
  quadruped (0.442 → 0.428). Girdle crossbars protrude by design (they reach the limb plates): 13-16 mm on the GT
  meshes, 27 mm on the lion (its rear girdle spans a diagonal body).
- **Ribs stop at the crown top**: the dorsal part of each section (above the backbone) is not covered by ribs.
- **Rib count is budget-limited** (piece-count target, sheet fit); the biped gets only 2 ribs.
- **No ground truth for real photos** (segmentation and 3D); procedural GT meshes are only 3 shapes.
- **Synthetic photos** (flat-colour fixtures) give unrepresentative TripoSR reconstructions.
- Phase 2 findings still open: limb chains dropped in 7/43 kit runs; vertebrae placed 180/438 requested.
  Egg-crate on AI meshes: with the cache fixed, fixtures `solid_ai3d` 3/6 → 4/6 ok (eagle now works, sliced
  horizontally); deer, thin_legs, gt_bird, gt_dino still fail `TOO_MANY_PIECES` at 30 → 16 pieces.
- **Machine sleep**: long harness runs on this laptop must run with the lid open (see §9 timings note).

## 8. Manufacturing assumptions and checks

- Flat stock, uniform thickness T (default 3 mm); kerf default 0.15 mm, clearance 0.10 mm.
- Slots: width = T + clearance, half-lap, engagement ≥ max(T, 3 mm) each side; min wall beside a slot max(T, 3 mm);
  min feature max(0.8·T, 2.5 mm); bowtie pocket = key + clearance/2 per side.
- **Kerf**: every cut path is the piece outline offset outwards by kerf/2 (`compensate_kerf`; measured by
  `fidelity.trace`: 0.0751 mm median offset for kerf 0.15). Slots therefore narrow by the kerf on the tool path
  and come out at nominal width after cutting.
- **CUT vs ENGRAVE** are separate SVG layers/paths (`*-cut`, `*-label`); labels never leave their piece.
- Every job runs `validate_model` (valid/simple/closed outlines, no duplicate segments, slot walls, narrowest
  feature, 3D interpenetration of every cross piece vs every plate, cross-piece crossings, bowtie fit, assembly
  connectivity), `validate_toolpaths`, `validate_layout` (inside the sheet margin, part spacing, no overlap); any
  error stops the job (no file is produced).
- One sheet per job (default 600 × 400 mm), 5 mm margin, 3 mm spacing; kits regenerate with fewer ribs/vertebrae
  (up to 3 times) before `PIECES_DO_NOT_FIT`.

## 9. Test methodology and current numbers

- `backend/tests` (pytest; AI models off by default, `-m ai` opt-in): unit, integration (HTTP), structural, real-3D.
- **`tests/test_mesh_usage.py` (requirement: maintain it)** — production upload path (remesh → prepare_mesh →
  MeshVolume → generate_puzzle with the mesh) on quadruped, slim quadruped (same side outline, body 0.6× as wide),
  biped and bird:
  - every rib is cut from the mesh section on its own plane, stays inside it, and is as wide as it;
  - same outline + different body → different ribs (width ratio 0.45-0.8), limbs and SVG;
  - different meshes → different bounding boxes, sections, parts, slot positions and SVG;
  - mesh-driven kit vs depth-only kit on the same mesh (harness voxel metrics): coverage@6mm, top IoU and material
    outside the body all better, front IoU not worse; quadruped front IoU > Phase 2 baseline 0.426;
  - off-centre body: ribs follow the asymmetric section and the limb plates sit deeper on the bulge side; the
    depth-only kit can only make symmetric ribs.
- `tests/meshes.py` holds the procedural meshes (quadruped, biped, bird) shared by tests and the harness.
- `backend/fidelity` (harness): side/front/top IoU, boundary F, Hausdorff/HD95, 3D voxel IoU, completeness,
  accuracy, Chamfer, coverage@3/6 mm, degradation counters; reports in `fidelity/reports/<run>/`.
- `fidelity.trace`: per-input stage dump (mesh stats, sections vs pieces, parts, slots, validation, nesting, kerf, SVG).

Measured (harness voxel metrics, 1 mm; kit vs the prepared mesh it was generated from; **mesh-driven / depth-only**
on the same mesh = the pre-fix hand-off; production upload path remesh → prepare_mesh; `python -m fidelity.ab_kit`;
legs at their measured depth, 2026-10-04):

| body | front IoU | top IoU | volume IoU | coverage@6mm | material outside body | Chamfer mm |
|---|---|---|---|---|---|---|
| quadruped | 0.580 / 0.437 | 0.421 / 0.358 | 0.127 / 0.077 | 0.625 / 0.369 | 0.132 / 0.465 | 6.17 / 8.38 |
| slim quadruped | 0.616 / 0.442 | 0.480 / 0.405 | 0.166 / 0.095 | 0.707 / 0.518 | 0.220 / 0.554 | 4.38 / 6.30 |
| biped | 0.396 / 0.194 | 0.228 / 0.173 | 0.131 / 0.060 | 0.711 / 0.363 | 0.570 / 0.801 | 4.99 / 10.66 |
| bird | 0.349 / 0.258 | 0.561 / 0.395 | 0.061 / 0.042 | 0.366 / 0.212 | 0.198 / 0.418 | 9.90 / 15.15 |
| off-centre bulge | 0.548 / 0.472 | 0.358 / 0.307 | 0.112 / 0.068 | 0.592 / 0.339 | 0.149 / 0.481 | 6.32 / 9.08 |

(The `reports/phase3_mesh_driven` harness run and the per-photo tables below were taken BEFORE the legs-at-measured-depth
change (limbs just outside the rib cage); re-run `fidelity.run` to refresh them.)

(Volume IoU is low by nature: a kit is a few 3 mm plates inside a solid body.)

Every cross piece vs the real mesh section on its plane (`fidelity.trace`, A/B with `--depth-only`, same input):

| input | ribs | rib width / section (median) | rib hull IoU with section | material outside body mm² (ribs / vertebrae / crossbars) | valid | kerf offset |
|---|---|---|---|---|---|---|
| quadruped | 7 → 7 | 0.96 → 1.00 | 0.59 → 0.76 | 760 / 259 / 1003 → 0 / 50 / 978 | yes | 0.0751 |
| biped (`--gt dino`) | 3 → 2 | 1.02 → 1.00 | 0.61 → 0.78 | 235 / 36 / 1038 → 0 / 0 / 881 | yes | 0.0751 |
| bird | 9 → 10 | 0.93 → 1.00 | 0.54 → 0.74 | 1035 / 116 / 889 → 1 / 10 / 946 | yes | 0.0751 |
| lion photo (TripoSR) | 10 → 11 | 0.49 → 1.00 | 0.28 → 0.67 | 1282 / 422 / 1365 → 147 / 422 / 1733 | yes | 0.0751 |

All of these: every piece placed inside the 600 × 400 sheet, CUT and ENGRAVE paths separate, validation OK.

### Fidelity harness: Phase 2 baseline vs this session (`reports/phase2` → `reports/phase3_mesh_driven`)

Compare with `python -m fidelity.compare fidelity/reports/phase2 fidelity/reports/phase3_mesh_driven`.
Per case (references: GT mesh for `gt_*`, the TripoSR reconstruction for fixtures/photos):

| case / mode | front IoU | top IoU | coverage@6mm | material outside | Chamfer mm |
|---|---|---|---|---|---|
| gt_quadruped kit_upload3d | 0.426 → 0.449 | 0.364 → 0.420 | 0.371 → 0.471 | 0.472 → 0.417 | 8.22 → 7.30 |
| gt_dino kit_upload3d | 0.181 → 0.220 | 0.160 → 0.170 | 0.361 → 0.381 | 0.807 → 0.788 | 10.45 → 8.83 |
| gt_bird kit_upload3d | **failed** → 0.332 | → 0.566 | → 0.392 | → 0.323 | → 12.23 |
| deer kit_ai3d | 0.039 → 0.141 | 0.151 → 0.169 | 0.154 → 0.269 | 0.916 → 0.800 | 19.19 → 16.16 |
| eagle kit_ai3d | 0.199 → 0.254 | 0.312 → 0.395 | 0.355 → 0.435 | 0.392 → 0.410 | 10.50 → 7.36 |
| logo kit_ai3d | 0.266 → 0.316 | 0.184 → 0.205 | 0.151 → 0.159 | 0.426 → **0.580** | 17.54 → **18.25** |
| trex kit_ai3d | 0.309 → **0.293** | 0.273 → 0.273 | 0.480 → **0.444** | 0.493 → 0.502 | 7.79 → 7.96 |
| gt_dino kit_ai3d (vs GT) | 0.239 → **0.202** | 0.221 → **0.196** | 0.515 → **0.383** | 0.781 → 0.793 | 7.56 → **9.04** |
| gt_bird kit_ai3d (vs GT) | 0.189 → **0.167** | 0.363 → **0.314** | 0.214 → **0.185** | 0.252 → 0.295 | 14.01 → 14.14 |

Reading: with an **uploaded model** (exact 3D) the kit is closer to the body on every case and the bird no longer
fails. With **AI 3D**, the kit now follows the reconstruction; that is closer to the reconstruction on most cases
(deer, eagle, irregular, thin_legs) but **further from the ground truth** for `gt_dino`/`gt_bird`, whose TripoSR
reconstructions (from flat-colour synthetic renders) are poor: following a wrong mesh more faithfully moves the kit
away from the real object. Reconstruction quality is now the limiting factor of the AI path.
Unchanged by design: `kit_estimated` / `solid_estimated` on fixtures (identical numbers). Egg-crate: AUTO
orientation can now pick horizontal slicing for meshes (it was always vertical because the stale cache broke the
horizontal analysis): `gt_bird solid_upload3d` front 0.962 → 0.216 but top 0.431 → 0.940 (layers seen from above),
`eagle solid_ai3d` failed → ok.

**Timings in `reports/phase3_mesh_driven` are not meaningful**: the run happened with the laptop lid closed
(`AppleClamshellState = Yes`), so the process was suspended for minutes at a time (a 60 s watchdog fired 16 min
late; the same case took 6 s and 907 s on two runs). Measured metrics are deterministic and unaffected.

**Real photos, AI 3D kit (`kit_ai3d`, 10 photos; reference = the TripoSR reconstruction, no ground truth exists):**
medians front IoU 0.383 → 0.483, top 0.293 → 0.395, volume IoU 0.048 → 0.061, coverage@6mm 0.209 → 0.348,
material outside 0.574 → 0.513, Chamfer 15.6 → 13.1 mm; side IoU unchanged (0.506 → 0.505).

| photo | front IoU | top IoU | coverage@6mm | material outside | Chamfer mm |
|---|---|---|---|---|---|
| cat_snow | 0.478 → 0.526 | 0.403 → 0.494 | 0.244 → 0.351 | 0.577 → 0.513 | 12.44 → 11.48 |
| eagle_head | 0.363 → 0.492 | 0.419 → 0.483 | 0.142 → 0.260 | 0.278 → 0.263 | 21.79 → 17.92 |
| lion_bw | 0.462 → 0.622 | 0.282 → 0.404 | 0.206 → 0.468 | 0.291 → 0.259 | 13.93 → 10.44 |
| lion_grass | 0.506 → 0.567 | 0.305 → 0.408 | 0.318 → 0.464 | 0.405 → 0.394 | 10.53 → 9.38 |
| lion_walk | 0.332 → 0.430 | 0.239 → 0.358 | 0.197 → 0.273 | 0.571 → 0.524 | 17.32 → 14.68 |
| owl_face* | 0.406 → 0.516 | 0.488 → 0.565 | 0.212 → 0.345 | 0.504 → 0.402 | 18.16 → 15.86 |
| parrot_head | 0.202 → 0.216 | 0.189 → 0.196 | 0.137 → 0.139 | 0.703 → 0.695 | 18.80 → 18.76 |
| penguin | 0.235 → 0.350 | 0.222 → 0.327 | 0.345 → 0.442 | 0.588 → 0.522 | 12.04 → 10.95 |
| trex_kit | 0.402 → 0.474 | 0.313 → 0.387 | 0.400 → 0.466 | 0.599 → 0.587 | 9.78 → 7.97 |
| zebra_head | 0.217 → 0.256 | 0.239 → 0.300 | 0.111 → **0.086** | 0.580 → 0.512 | 26.37 → 22.42 |

\* owl_face: GrabCut segmentation is not bit-reproducible (confidence 0.461 vs 0.465 between runs), so its input
differs slightly between the two runs; that also changed its `kit_estimated` / `solid_estimated` results. Two other
photos differ in `kit_estimated` by ≤ 0.0002 side IoU (same piece counts). Everything else in the estimated paths is
identical. Before/after picture of the lion: `backend/fidelity/reports/lion_before_after.png`.

## 10. How to run and test

```bash
# backend
cd backend && .venv/bin/uvicorn app.main:create_app --factory --port 8000
# frontend
cd frontend && npm run dev            # http://localhost:3000
# docker (postgres + backend + frontend)
docker compose up --build

# tests
cd backend && .venv/bin/python -m pytest                         # all backend tests (AI off)
.venv/bin/python -m pytest tests/test_mesh_usage.py -o addopts="" # mesh-usage guard (~30 s)
.venv/bin/python -m pytest fidelity/tests -q -o addopts=""        # harness self-tests
cd frontend && npm test && npm run typecheck && npm run lint && npm run build

# measurement / diagnostics
cd backend
.venv/bin/python -m fidelity.trace --gt quadruped --out /tmp/t   # or --image photo.jpg / --model model.stl
.venv/bin/python -m fidelity.trace --gt quadruped --depth-only --out /tmp/t0   # A/B: pre-fix hand-off (no sections)
.venv/bin/python -m fidelity.ab_kit                              # A/B voxel table: mesh-driven vs depth-only (~3 min)
.venv/bin/python -m fidelity.run --out fidelity/reports/<name>    # ~25-30 min, all cases
.venv/bin/python -m fidelity.report --dir fidelity/reports/<name>
```

## 11. Important files

| File | Role |
|---|---|
| `backend/app/services/pipeline.py` | generate_puzzle: kit/solid dispatch, **mesh → KitGenerator hand-off**, validation, kerf, nesting, exports |
| `backend/app/services/job_service.py` | jobs, uploads (`_upload_model`), AI 3D gating (`_body_mesh`) |
| `backend/app/geometry/volume.py` | Volume / DepthVolume / MeshVolume, plane sections, `_bodies` cache, remesh, prepare_mesh, alignment |
| `backend/app/geometry/kit/builder.py` | KitGenerator: backbone, skull, limbs, ribs (section-based), vertebrae, joint acceptance, diagnostics |
| `backend/app/geometry/kit/bones.py` | BoneLibrary incl. `section_rib` |
| `backend/app/geometry/kit/structure.py` | StructureAnalyzer → AnimalStructure (spine, head, limbs, editable skeleton) |
| `backend/app/geometry/kit/components.py` | joints, connectors, spacers, skull/jaw/limb helpers, `clean` |
| `backend/app/geometry/engine.py` | egg-crate PuzzleGenerator (solid style) |
| `backend/app/validators/geometry_validator.py` | all manufacturing validation |
| `backend/tests/test_mesh_usage.py` | mesh-usage regression guard |
| `backend/tests/meshes.py` | procedural meshes: quadruped, biped, bird |
| `backend/fidelity/` | measurement harness (`run`, `report`, `trace`, `metrics`, `assembly`, `instrument`) |

## 12. TODO / next priorities (in order)

1. **Re-run and read the harness** after every geometry change (`fidelity.run` → compare with
   `reports/phase2` and `reports/phase3_mesh_driven`).
2. **Axis off the spine plane**: align the kit plane with the body (e.g. rotate the mesh about y so the torso
   section centres lie on z = 0 when the drift is a straight line), or split the backbone into z-offset segments.
   Measure first with the lion photo (section centre drift −19.5 → +24.4 mm).
3. **Limb plates at the measured leg depth** when narrowing the ribs near the girdle is acceptable (evaluate on
   biped/bird: material outside 0.79 / 0.30).
4. **Dorsal coverage**: neural spines on torso ribs/vertebrae up to the measured dorsal surface of the section.
5. Skull plate depth and outline from head sections (today: depth field + side outline).
6. Phase 2 open items: dropped limb chains, vertebra placement rate, egg-crate AI-3D failures (re-measure first).
