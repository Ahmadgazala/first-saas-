# Algorithm decisions (and why)

Each entry: the decision, the reason (with the measurement or incident behind it), and what it costs. Numbers come from
the fidelity harness (`backend/fidelity/`) or the scripts in `scratchpad/`; see `docs/known-issues.md` for open problems.

## Product and architecture

**D1 — Generic engine, no species templates.** The slice/kit generators derive everything from the detected structure
and the 3D body; there is no `if lion:`. *Why:* the product must work from one photo of any object. *Cost:* structure
detection is heuristic and fails on unusual poses.

**D2 — The 3D mesh must drive the kit (not only its outline).** The mesh path used to pass only `volume.silhouette` and a
symmetric depth to the kit generator, so ribs were templates (49 % of the real section width on the lion photo, up to 28 mm
outside the body). Now `KitGenerator.generate(..., volume=...)` and every cross piece is cut from the mesh section on its
own plane. `tests/test_mesh_usage.py` fails if the mesh is ignored. *Measured:* rib width/section 0.49 → 1.00, material outside
the body 3069 → 2302 mm² (lion), quadruped front IoU 0.437 → 0.580 vs the depth-only kit.

**D3 — Solid slice is the default style; skeleton kit and egg-crate stay.** The reference product (photographed T-Rex kit) is a
body plate with many *solid* slices, not thin arch ribs. *Why kept:* the skeleton kit is a valid alternative; nothing was
deleted.

**D4 — Single-photo depth is a user control.** A photo cannot show thickness, so `depth_scale` (0.3–3) scales the estimated
depth field (photo only) or the mesh z (AI 3D/upload) and visibly changes slice widths, skull/limb depth and slots (tested).

**D5 — Manufacturing validity before looks.** Every export passes `validate_model` / `validate_toolpaths` / `validate_layout`; a
failed validation is an error, never a silent export. Kerf is applied as an outward offset of kerf/2 on every cut path.

## Geometry

**D6 — Slot geometry: half-lap, width = T + clearance, meeting at the split.** Standard egg-crate joint; `_try_cross` checks
every joint and every 3D relation before cutting, so the generator and the validator use the same rules.

**D7 — Slices are solid sections, spacing from the material.** Pitch = slot + wall + 2 mm times a Detail factor
(2.6 / 1.8 / 1.15), up to 40 % denser where height × width changes quickly; never below the physical minimum. *Why:* "every
additional piece has a purpose"; a fixed count ignores size and shape.

**D8 — Slices are placed greedily one gap after the previous placed slice, with tilted planes as fallback.** Perpendicular
slices of a curved spine converge on the inside and intersect; trying ±10…45° (and parallel-to-previous) resolved collisions.
*Known cost:* order sensitivity (shifting the start by a few mm changes the result; see known-issues KI-4).

**D9 — Narrow regions: skip the slice, let the spine plate take over.** Where the body is too thin for a slot with walls
(≈ 8.6 mm at T = 3), no slot is forced; the plate grows to ~92 % of the local height so the side silhouette is kept.
*Measured:* side IoU +0.003 (depth ×1.0), +0.008 (×0.6), +0.023 (×0.4) on the reference pose.
*Mesh volumes:* the width is the **real mesh section on the slice plane**, not `spine.half_depth` — the latter is
`min(up, down)` around z = 0 and collapses to a few mm for any body that is off the z = 0 plane (gt_dino: 5.9 mm vs 15.5 mm;
penguin 4.8 mm vs 64 mm), which skipped valid slices. *Estimated volumes keep the old measure*: the inflated body is
symmetric, so 2 × depth *is* the slice width.

**D10 — Limbs follow the image outline; legs stand at their measured depth.** Limb plate = the limb polygon (smoothed,
min-feature opened) ∪ a blade around the girdle strut; for meshes the leg depth comes from `z_intervals`. *Measured:* legs
at the measured depth instead of just outside the rib cage: quadruped front IoU 0.455 → 0.580, material outside 0.38 → 0.13.

**D11 — Appendages (arms, claws) hang from the body at their own station.** They used the legs' "outside the widest rib"
depth, which put plates and crossbar outside the body (T-Rex: plates z = ±24.7, crossbar ±29.2 vs reference half-depth
11–15 mm; front IoU −0.043). Now clamped to the local half-depth + T/2, never below the spacer clearance. Detection itself is
unchanged.

**D12 — Head details come from the outline, never from a template.** Mouth, jaw and teeth are traced from the open mouth of
the outline (the head is no longer Chaikin-smoothed, so teeth survive); no eye/nostril/window is generated. Openings that are
holes of the outline inside the head are cut out of the skull plates. *Why:* "do not invent geometry without evidence".
The mouth scan runs on the head **with holes filled**: a hole between two stretches of material is not a gap between jaws
(it was chosen over the real mouth at two samples and destroyed the opening before any spacer was placed).

**D13 — Segmentation keeps small holes; other styles fill them.** `clean_mask` keeps holes ≥ 0.1 % of the object (were
filled below 2 %) so eye sockets/windows survive; `fill_small_holes` restores the old behaviour for egg-crate and skeleton kit.
The photographed kit's mask has 4 such holes, one in the head.

**D14 — Head found by neck constriction when it is merged into the torso blob.** Dinosaurs, lions and elephants have heads that
the torso-opening heuristic swallows; the tail identifies the other end and the head is the bulge beyond the narrowest point of
the width profile. Legs may stop up to 22 % of the height above the ground (a raised foot used to leave < 2 legs → "not an
animal" → no head/tail at all).

## Process

**D15 — Measure before changing; keep every run visible.** The fidelity harness is observation-only and proven output-identical
to production. Because slice placement is order-sensitive, regression tests run several start positions and report each run
plus median/range; floors apply to every run.

**D16 — Public repository hygiene.** Uploads, generated output, reconstructions of local photos, model weights, videos > 100 MB and
machine-specific agent config are git-ignored (`.gitignore`).
