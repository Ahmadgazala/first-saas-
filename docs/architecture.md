# Architecture

PuzzleCut is a monorepo of two applications plus a measurement harness:

```
frontend/   Next.js 16 / React 19 / Three.js — upload, settings, previews, downloads
backend/    FastAPI (Python 3.12) — jobs, vision, reconstruction, geometry, validation, nesting, export
  app/        the application
  tests/      pytest suite (AI models off by default)
  fidelity/   standalone measurement harness (runs the production code, never modifies it)
  third_party/TripoSR  vendored photo → 3D model (MIT)
```

## End-to-end data flow

```
photo ──► load/validate ──► segmentation ──► mask (+ holes ≥ 0.1 % of the object)
                              │                         │
          rembg IS-Net +      │                         ├─► suitability (blocking: no subject / several / cut off)
          classical candidates│                         │
                                                        ▼
        shape_source = auto │ ai_3d │ silhouette
              │                                  (3D model upload: remesh → orient → prepare_mesh)
              ▼
   TripoSR reconstruction ─► remesh ─► align to the photo (24 axis rotations) ─► prepare_mesh
              │             gates: alignment IoU ≥ 0.55, depth ratio ≥ 0.14, else the estimate is used
              ▼
   Volume = MeshVolume (real mesh)  |  DepthVolume (silhouette inflated by the depth model, scaled by `depth_scale`)
              │
              ▼
   structure analysis: spine curve, head / neck / torso / tail, limbs, thin appendages
              │
              ▼
   generator (style): slice (default) │ kit (skeleton) │ skeleton / solid (egg-crate)
              │         → PuzzleModel: plates (z = const) + cross pieces (RibFrame) + joints (half-lap slots)
              ▼
   validate_model ─► kerf compensation (tool paths) ─► validate_toolpaths ─► optional kit frame
              ─► nest (raster bottom-left fill) ─► validate_layout
              ▼
   exporters: laser_sheet.svg / .dxf / .pdf, assembly guide PDF, metadata.json, 3D model JSON, preview PNG
```

The API never runs geometry in a request: `POST /api/jobs/{id}/generate` submits `run_generation` to a
`TaskQueue` (in-process thread pool for the MVP; the interface allows Celery/Redis) and the client polls `GET /api/jobs/{id}`.

## Backend modules (`backend/app/`)

| Package | Responsibility |
|---|---|
| `api/` | `routes.py` (jobs, upload, silhouette re-extraction, analyze, generate, downloads), `schemas.py` (`PuzzleSettingsIn`: material, thickness, kerf, clearance, size, pieces, complexity, style, `depth_scale`, kit options, skeleton) |
| `services/` | `job_service.py` (job lifecycle, settings ↔ `PuzzleParameters`, AI-3D gating `_body_mesh`), `pipeline.py` (`generate_puzzle`: generator dispatch, validation, kerf, nesting, exports; kit sheet-fit retry), `storage.py` (filesystem abstraction, S3-ready), `task_queue.py` |
| `models/` | SQLAlchemy entities (job, settings, generated files), tiny forward migration for added columns (`db.py`) |
| `vision/` | `segmentation.py` (candidates + scoring + hole handling), `suitability.py` (is this photo usable; structure overlay), `image_io.py` |
| `reconstruction/` | `triposr.py`, `service.py` (alignment, depth ratio, gates) |
| `geometry/` | `normalize.py` (mask → polygon in mm), `depth.py` (depth fields), `volume.py` (Depth/MeshVolume, sections), `anatomy.py`, `slots.py` (slot sizing, kerf), `nesting.py`, `quality.py` (measured manufacturing metrics), `engine.py` (egg-crate), `kit/` (structure, builder, slice, bones, components, constraints, frame), `labels.py`, `types.py` |
| `validators/` | `geometry_validator.py` (model, tool paths, layout), `file_validators.py` (uploads) |
| `exporters/` | `sheet.py` (flattened sheet geometry shared by all formats), `svg.py`, `dxf.py`, `pdf.py`, `assembly_guide.py`, `metadata.py`, `render.py` (preview / 3D shading) |

## Frontend (`frontend/src/`)

`app/` (routes: `/`, `/create`, `/jobs/[id]`, `/pricing`, `/about`), `components/` (`CreateWorkspace` orchestrates upload →
silhouette → settings → generate → results; `SettingsPanel` incl. style cards and the Object depth slider;
`SkeletonEditor`; `PuzzleViewer` Three.js assembled/exploded view; `ResultViews`), `lib/` (`api.ts` client, `settings.ts`
defaults + validation mirroring the API, `types.ts`). Default style is **Solid slice**, piece count default 40.

## Data and configuration

* Postgres in Docker; SQLite in tests. Uploads and outputs under `STORAGE_DIR` (`backend/data/` locally — git-ignored).
* Configuration is environment-driven (`.env.example`): DB URL, CORS origins, upload limits, reconstruction backend/device,
  segmentation model, log level. Frontend: `NEXT_PUBLIC_API_URL`.
* AI is optional (`pip install -e ".[ai]"`): without it, classical segmentation and the estimated body are used; uploaded
  3D models are always sliced exactly. No paid API is used anywhere.

## Manufacturing model

Flat stock of uniform thickness T (default 3 mm), kerf default 0.15 mm, clearance 0.10 mm. Slot width = T + clearance;
tool path = outline offset outwards by kerf/2; slots then come out at nominal width after cutting. Minimum feature
`max(0.8·T, 2.5 mm)`, minimum wall `max(T, 3 mm)`, engagement ≥ `max(T, 3 mm)`. One sheet per job (default 600 × 400 mm,
5 mm margin, 3 mm spacing). Every export is gated by validation.

## Measurement harness (`backend/fidelity/`)

Runs the production pipeline per case and mode (`*_estimated`, `*_ai3d`, `*_upload3d`) and measures silhouette IoU,
boundary-F, Hausdorff, 3D voxel IoU, front/top projection IoU, coverage and Chamfer against a reference (ground-truth
mesh, the TripoSR reconstruction, or the generator's own depth estimate). It is observation-only: `InstrumentedKit`
subclasses the generator and is proven output-identical to production by its own tests.
