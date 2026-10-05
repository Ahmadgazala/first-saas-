# PuzzleCut

Turn **one photo or one 3D model** into a **laser-cut, slot-together 3D kit**: validated vector geometry
(SVG / DXF / PDF), nested on a single sheet, with an assembly guide. Everything runs locally; no paid API is used.

> The previous long README is kept in [`docs/legacy-readme.md`](docs/legacy-readme.md). Technical detail lives in [`docs/`](docs/).

## The problem

A photo shows only one side of an object. PuzzleCut estimates (or reconstructs) its 3D body, builds a kit of flat plates
that interlock through half-lap slots, and outputs files a laser cutter can use directly: kerf-compensated cut paths,
engraved part numbers, nothing that cannot be cut, nothing that cannot be assembled.

## What it does today

* **Inputs:** a photo (PNG/JPG/WEBP) or a 3D model (STL / OBJ / GLB / PLY).
* **3D body:** local AI reconstruction ([TripoSR](https://github.com/VAST-AI-Research/TripoSR), optional), a depth
  estimate from the silhouette, or the uploaded mesh (exact). **Object depth** is a user control.
* **Styles:** *Solid slice* (default: body-shaped spine plate + dense solid cross-section slices + limb plates from the
  image outline + skull with traced mouth/teeth), *Skeleton kit*, and two egg-crate styles.
* **Manufacturing:** material thickness, kerf, clearance, minimum feature/wall rules, half-lap slots, bowtie connectors,
  deterministic nesting, optional kit frame. Every export is gated by geometry validation.
* **Outputs:** `laser_sheet.svg` (layers `CUT` / `ENGRAVE`, 1 unit = 1 mm), `.dxf`, `.pdf`, assembly guide PDF, 3D preview.
* **UI:** upload, silhouette check, structure/skeleton editor, settings, 3D assembled/exploded preview, downloads.

## Architecture (one screen)

```
frontend/  Next.js 16 · React 19 · Three.js          upload → settings → preview → download
backend/   FastAPI · Shapely · trimesh · ezdxf        vision → reconstruction → structure → generator
                                                       → validation → kerf → nesting → SVG/DXF/PDF
  app/geometry/kit/slice.py                           the default "Solid slice" generator
  fidelity/                                           measurement harness (IoU / coverage vs references)
docs/       architecture, geometry pipeline, algorithm decisions, known issues, roadmap
scratchpad/ historical analysis scripts (not production)
```

See [`docs/architecture.md`](docs/architecture.md) and [`docs/geometry-pipeline.md`](docs/geometry-pipeline.md).

## Run locally

Requirements: Python 3.12, Node 20+, PostgreSQL (or Docker). Copy `.env.example` → `backend/.env` and
`frontend/.env.example` → `frontend/.env.local`.

```bash
# backend
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"     # add ".[ai]" for AI segmentation + photo→3D (large downloads)
.venv/bin/uvicorn app.main:create_app --factory --port 8000    # http://localhost:8000/api/health

# frontend (second terminal)
cd frontend && npm install && npm run dev                      # http://localhost:3000

# or everything with Docker
docker compose up --build
```

## Run the tests

```bash
cd backend && .venv/bin/python -m pytest                       # ~390 tests, AI models off
cd frontend && npm test && npm run typecheck && npm run lint   # 56 tests
```

Geometry changes also need the measurement harness: see "Run and test" in [`CLAUDE.md`](CLAUDE.md).

## Where the important code lives

| What | Where |
|---|---|
| Job pipeline (generate → validate → kerf → nest → export) | `backend/app/services/pipeline.py` |
| Solid-slice generator | `backend/app/geometry/kit/slice.py` (on top of `kit/builder.py`) |
| Structure detection (spine, head, limbs, arms) | `backend/app/geometry/kit/structure.py`, `anatomy.py` |
| Mesh / depth volumes and sections | `backend/app/geometry/volume.py` |
| Slots, kerf, nesting | `backend/app/geometry/slots.py`, `nesting.py` |
| Validation | `backend/app/validators/geometry_validator.py` |
| SVG / DXF / PDF export | `backend/app/exporters/` |
| Settings UI | `frontend/src/components/SettingsPanel.tsx`, `CreateWorkspace.tsx` |
| Regression tests | `backend/tests/` (`test_slice.py`, `test_mesh_usage.py`), `backend/fidelity/tests/` |

## Development status

Working end to end (photo or model → validated SVG/DXF). Active work is geometry quality of the *Solid slice* style.
Three measured defects (mesh `_narrow()` proxy, appendage depth, mouth-gap vs head opening) are fixed in the working
tree (backend suite: 391 tests passing); known open issues and the roadmap are in
[`docs/known-issues.md`](docs/known-issues.md) and [`docs/roadmap.md`](docs/roadmap.md). Not claimed production-ready:
geometry is validated for manufacturability on every job, but visual fidelity to the subject is limited and measured.

AI contributors: read [`CLAUDE.md`](CLAUDE.md) first.

## Licences

TripoSR is MIT (vendored in `backend/third_party/TripoSR`); weights come from Hugging Face on first use. IS-Net/rembg
(Apache-2.0 / MIT) for background removal. See `backend/third_party/README.md`.
