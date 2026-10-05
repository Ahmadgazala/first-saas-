# Fidelity harness (Phase 2: measurement only)

Standalone harness that runs the **existing** production pipeline and measures how closely the
assembled kit resembles the reference. Nothing in `app/` is modified: the kit generator is
observed through a subclass (`instrument.py`), proven output-identical by
`tests/test_harness.py::test_instrumented_kit_is_identical_to_production`.

```bash
cd backend
.venv/bin/python -m pytest fidelity/tests -q -p no:warnings -o addopts=""   # harness self-tests
.venv/bin/python -m fidelity.run [--only horse,photo_lion_walk] [--res 1.0]  # ~25 min for all cases
.venv/bin/python -m fidelity.report                                          # results.csv / report.md
.venv/bin/python -m fidelity.trace --gt quadruped --out /tmp/t [--depth-only]  # one input, every stage (A/B)
.venv/bin/python -m fidelity.ab_kit                                          # kit from mesh sections vs outline+depth only
```

Reports: `reports/phase2/` = baseline before the mesh-driven kit; `reports/phase3_mesh_driven/` = after
(see PUZZLECUT_CONTEXT.md).

Output: `fidelity/reports/phase2/` → `cases/<case>.json` (all per-case, per-mode, per-stage values),
`results.json`, `results.csv`, `report.md`, `overlays/<case>__<mode>.png` (grey = reference,
blue = assembled kit; side view, plus front/top when a 3D reference exists).
TripoSR reconstructions are cached in `fidelity/cache/` (app uploads' own cached reconstructions are reused).

## Cases
| kind | 2D reference | 3D reference |
|---|---|---|
| fixture (11 drawn masks, fed as `mask_to_photo`) | ground-truth mask | none (kit compared with the generator's own estimated body) |
| gtmesh (quadruped, dino, bird; procedural) | GT orthographic side mask | GT mesh |
| photo (10 real photos) | pipeline's own segmentation (no GT) | TripoSR reconstruction when it passes the production gates |

## Modes (web-app default settings: 3 mm, 250 mm, 30 pieces, advanced, 600×400, frame on)
`kit_estimated`, `solid_estimated` (shape_source=silhouette) · `kit_ai3d`, `solid_ai3d` (production
`_body_mesh` gates) · `kit_upload3d`, `solid_upload3d` (production 3D-upload path, GT meshes only).
Egg-crate (`solid`) falls back to 16 pieces when 30 fails with TOO_MANY_PIECES (noted per result).
Generation failures and unhandled production exceptions are recorded as results.

## Metrics
* 2D (side = reference camera, exact vector projection of every slab): IoU, Boundary-F (τ = 2 % of
  size = 5 mm; 1 % reported as `bf_tight`), Hausdorff / HD95 / ASSD, area recall/precision,
  per-anatomical-region recall, and the same on the **envelope** (closing radius 6 mm, a proxy for
  the outline perceived from a sparse skeleton).
* 3D (1 mm voxels): volume IoU, front/top/side projection IoU, completeness (reference surface →
  nearest kit material), accuracy (kit surface → reference surface), Chamfer = mean of both,
  coverage@3/6 mm, share of kit material outside the reference.
* Stages: segmentation → largest component → simplified → opened (min feature) → reinforced →
  normalized (traced; asserted equal to production) · reconstruction alignment / depth gate ·
  2.5D body (silhouette + symmetric half-depth, what the kit generator receives) vs the mesh ·
  remesh vs GT (upload path).
* Degradation (kit): ribs / vertebrae requested vs placed, shrunk candidates, dropped plates,
  trimmed axis share, sheet-fit retries, and the counterfactual "intended" kit (first candidate of
  every shrunk/failed piece + dropped plates) re-measured in side, front and 3D.

## Known limits of the measurement
No ground truth for real-photo segmentation or real-photo 3D; GT meshes are 3 procedural shapes;
reconstructions of the synthetic (flat-colour) photos are not representative of real photos;
egg-crate internal losses (`_cleanup`, rib selection) are not instrumented; perception and
physical assembly are not measured.
