# Geometry pipeline

How data moves from a mask or mesh to cut paths. Units: millimetres. Frame: **x along the body, y up, z depth**,
spine plate at z = 0. Plates are profile pieces at z = const (local `(u, v) = (x, y)`); cross pieces (slices, ribs,
spacers) lie on a `RibFrame(cx, cy, dx, dy)`: the plane containing the z axis through `(cx, cy)` with in-plane direction
`(dx, dy)`, local `(u = z, t)`.

## 1. Mask → silhouette (`vision/segmentation.py`, `geometry/normalize.py`)

* Segmentation candidates (IS-Net, alpha, colour model, GrabCut, threshold, edges) are cleaned (`clean_mask`) and scored.
  **Interior holes ≥ 0.1 % of the object are kept** (they were filled below 2 % before); eye sockets, nostrils and skull
  windows can therefore reach the geometry.
* `normalize_silhouette(mask, size, min_feature)` → polygon in mm (largest component, simplified, thin features opened
  or reinforced). `fill_small_holes(poly, 0.02)` gives the pre-change behaviour; **egg-crate and skeleton kit use it**, the
  slice style keeps the holes.

## 2. Body: `Volume` (`geometry/volume.py`)

* `DepthVolume(silhouette, depth)` — estimated: the silhouette inflated by the depth model, symmetric about z = 0, scaled
  by the user's `depth_scale`. `cross_section` samples the depth field along the section line.
* `MeshVolume(mesh)` — a real mesh (TripoSR or upload) after `prepare_mesh` (rotate to the canonical frame, scale to the
  size, rest on y = 0, centre x, z centre of mass at 0). `plane_section` gives exact sections (shells cached per vertex-hash;
  the cache key matters: `trimesh.copy()` copies metadata). `z_intervals(x, y)` gives the solid's z extents on a line.
  `scale_depth` implements the depth control for meshes.
* **`spine.half_depth` is `min(up, down)` around z = 0 — a symmetric proxy.** Correct for a `DepthVolume`; wrong for a mesh
  whose body is off the z = 0 plane (use the real section instead — see `docs/algorithm-decisions.md`).

## 3. Structure (`geometry/anatomy.py`, `geometry/kit/structure.py`)

`analyze(silhouette)` → regions (torso = opened blob; appendages classified LIMB / HEAD / TAIL / EXTREMITY by attachment
and direction; legs may stop up to 22 % of the height above the lowest line). `StructureAnalyzer.analyze` →
`AnimalStructure`: spine curve (longest path of the axial skeleton, oriented tail → head, with dorsal/ventral distances and
`half_depth`), ranges (head / neck / torso / tail), `LimbStructure`s with joints, `HeadStructure`.

* Head merged into the torso blob: found as the bulge beyond the **neck constriction** of the width profile.
* `thin_appendages=True` (slice style only): small ventral appendages (0.15–3.5 % of the area, ≥ 4 mm thick) are merged by
  attachment distance and become limbs of kind `appendage`.
* A hand-edited skeleton (frontend `SkeletonEditor`) replaces the detected spine/head/limbs.

## 4. Generators

All kit generators share `KitGenerator` (`kit/builder.py`): plates dict, cross pieces, `_try_cross` (accepts a cross piece
only if every joint and 3D relation is valid: slot through to the edge, engagement, walls, narrowest feature, no
crossing of another cross piece, no interpenetration with any plate; then cuts the slots), bowtie connectors for coplanar
seams, `_finish` (IDs, labels, analysis, `kit_volume` diagnostics).

### Slice style (`kit/slice.py`, default)

1. **Spine plate** (`_hb`): half height 28 % of the local body height (min bar height); inherited `_backbone` splits it
   into segments joined by bowties when longer than the sheet allows. Where the body is too thin for a slot (`_narrow`),
   the plate grows to ~92 % of the local height (8 mm ramp) instead of forcing a slot.
2. **Slices** (`_ribs_and_vertebrae`, `_gap_profile`, `_try_slice`, `_slice_section`, `bones.slice_plate`): solid section
   of the volume on the plane perpendicular to the spine, clipped belly (limb-free outline) → top of the back, cavities
   filled, thin features removed. Spacing = (slot + wall + 2 mm) × Detail factor, denser where the section changes fast,
   capped by "max pieces". Each slice is placed one gap after the previous *placed* slice; plane directions tried:
   perpendicular, parallel to the previous slice, ±10…45° (curved spines make perpendicular slices converge); width
   narrowed (0.85, 0.7) if a limb/skull plate is in the way.
3. **Limbs**: plate = the limb's own outline (smoothed, features below the minimum removed) ∪ a blade around the girdle
   strut; legs stand at their measured depth (`limb_mode = "measured"`: `z_intervals`), ribs near a girdle are narrowed
   to fit between the plates. **Appendages** (`_girdle_depth` + `_appendage_half_extent`) are clamped to the local
   body half-depth + T/2 (never below the depth a spacer needs to clear the backbone plate).
4. **Head** (`_skull`): skull plates at ±zs from the head polygon (not smoothed, so teeth survive); **mouth** traced by
   `_traced_mouth` (cross line meeting two stretches of material → cut along the gap midline to the snout and straight
   back from its apex; scan on the head with holes filled; seam kept clear of holes; falls back to the generated mouth);
   holes in the head are cut out of the skull plates; skull spacers move away from openings and fall back to the default
   stations. `invent_openings = False`: no eye/nostril is created.

### Skeleton kit (`kit/builder.py`) and egg-crate (`engine.py`)

Kept and selectable; the kit builds thin arch ribs/vertebrae/designed bones; egg-crate slices the volume into
profile plates + cross ribs. They do not use the slice-only features (thin appendages, traced head, narrow rule).

## 5. Validation → kerf → nesting → export (`services/pipeline.py`)

`validate_model` (outlines valid/simple/closed, slot walls and widths, narrowest feature, 3D interpenetration, bowties,
connectivity) → `compensate_kerf` per piece → `validate_toolpaths` → optional `FrameGenerator` → `nest` →
`validate_layout` → `exporters/sheet.py` flattened sheet → SVG (`CUT` red hairline closed paths, `ENGRAVE` blue open
paths, 1 unit = 1 mm), DXF (R2010, mm, closed LWPOLYLINEs, re-read and audited), PDF, assembly guide. Kit styles
regenerate with fewer pieces (×0.75, slice ×0.7, up to 3 / 7 times) before reporting `PIECES_DO_NOT_FIT`.

## 6. Diagnostics

`model.analysis` carries `style`, `component_counts`, `structure_model`, `connectors`, and for kits `kit_volume`
(per-rib source and section width, limb group depths incl. appendage clamp), `slices` (planned / placed / narrow share),
`traced` (head openings, arms, mouth). `python -m fidelity.trace` dumps every stage for one input.
