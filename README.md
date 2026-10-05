# PuzzleCut AI

**Turn one suitable photo into a laser-cut, slot-together fossil-style kit - or a 3D egg-crate puzzle.**

```
ONE SUITABLE PHOTO ─► IMAGE / STRUCTURE ANALYSIS ─► PROCEDURAL 3D KIT ─► LASER-CUT COMPONENTS
                   ─► SVG / DXF / PDF ─► PHYSICAL ASSEMBLY ─► RECOGNIZABLE 3D OBJECT
```

The default **Fossil kit** style builds, from the detected structure of *any* animal or object (no species
templates): a segmented backbone joined by bowtie connectors, individual vertebrae, open arch ribs, skull side plates
with real eye-socket / nostril openings, a separate lower jaw with teeth, separate limb bones, spacers, an optional
kit frame with the engraved name, and a white-material 3D preview. The output is an **estimated structural model**,
not an accurate 3D reconstruction of the animal (see limitations).

Upload a **photo or a 3D model** (STL / OBJ / GLB / PLY) → review the silhouette → choose material, kerf, clearance,
piece count and sheet → download
validated manufacturing files: `laser_sheet.svg`, `laser_sheet.dxf`, `laser_sheet.pdf` and `assembly_guide.pdf`,
plus a rendered `preview.png`, `metadata.json` and an interactive Three.js preview built from the same geometry.

This is not an image tracer. A photo is turned into a **real 3D model by AI** (TripoSR, running locally), or an
uploaded model is used directly; the slicer then cuts the body into interlocking flat pieces with exact plane
sections. (Flat artwork, or servers without the AI extras, fall back to a 3D body estimated from the silhouette.)
The backend builds a structural model of interlocking ribs (profile ribs + cross ribs), cuts mathematically sized half-lap slots, compensates for kerf, validates every piece and the 3D
assembly, and nests the parts on your sheet. A file is only offered for download after all validation has passed.

---

## Architecture

```
frontend/  Next.js 16 · TypeScript · Tailwind 4 · Three.js         (UI, 3D viewer)
backend/   Python 3.12 · FastAPI · SQLAlchemy · PostgreSQL          (API, jobs, storage)
  app/
    api/          REST endpoints + Pydantic schemas (thin layer)
    core/         settings, error codes, logging
    models/       SQLAlchemy entities: User, Project, PuzzleJob, PuzzleSettings, GeneratedFile
    services/     job lifecycle, pipeline orchestration, StorageService, TaskQueue
    vision/       safe image decoding, multi-strategy segmentation, ImageSuitabilityAnalyzer
    geometry/     framework-independent engine: normalize, anatomy, depth model, volume (mesh sections),
                  structure/ribs, slots/kerf, labels, nesting, quality metrics
      kit/        fossil-kit system: StructureAnalyzer (AnimalStructure), ManufacturingConstraints,
                  Rib/Vertebra/Spine/Skull/Jaw/Limb/Tail/Wing/Joint/Connector/Spacer generators,
                  KitGenerator (builder), FrameGenerator
    reconstruction/  photo -> 3D mesh (TripoSR wrapper, alignment, flatness check)
  third_party/TripoSR/  vendored TripoSR (MIT), see third_party/README.md
    validators/   upload validation, geometry validation & safe repair
    exporters/    SVG, DXF (ezdxf), PDF (reportlab), renderer, assembly guide, metadata/model JSON
  tests/          unit + integration tests (pytest)
docker-compose.yml   postgres + backend + frontend
```

Processing pipeline (`app/services/pipeline.py`):

```
image ─► safe decode (Pillow, EXIF, bomb guard)
      ─► segmentation candidates: AI (rembg IS-Net) · alpha · background colour model · GrabCut · Otsu · Canny
      ─► cleanup (morphology, main component, hole fill) ─► scoring + confidence ─► silhouette.png (user reviews)
      ─► normalize to mm (contour, simplify, reinforce thin features / drop hairlines, centre, scale)
      ─► 3D body: AI reconstruction (TripoSR) → repair + align to the photo  |  uploaded model  |  estimate
      ─► anatomy (regions, skeleton axes, curvature) ─► volume (exact mesh sections or region-aware depth field)
      ─► spine + side profiles (legs) + detail plates + adaptive oriented cross ribs ─► half-lap joints/slots
      ─► cleanup + connectivity ─► labels (engrave) ─► VALIDATE model, slot walls, 3D assembly
      ─► kerf compensation ─► VALIDATE tool paths ─► nesting ─► VALIDATE layout
      ─► quality metrics ─► SVG / DXF / PDF ─► preview.png ─► assembly_guide.pdf ─► metadata.json / model.json
```

The geometry engine (`app/geometry`) has no dependency on FastAPI or the database. Generation runs outside the
request through a `TaskQueue` (in-process thread pool today; a Celery/Redis implementation only needs `submit`).
All file IO goes through `StorageService` (local filesystem today; S3/R2/Supabase later).

---

## Prerequisites

- Python **3.12** (the project uses [uv](https://docs.astral.sh/uv/); plain `pip`/`venv` works too)
- Node.js **20+** (tested with 24) and npm
- PostgreSQL **14+** (tested with 17)
- Optional: Docker + Docker Compose

## Quick start (local)

```bash
# 1. Database
createdb puzzlecut

# 2. Backend
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,ai]"   # drop ",ai" for a small install without PyTorch
cp ../.env.example .env          # edit DATABASE_URL if your Postgres needs a user/password
.venv/bin/uvicorn app.main:create_app --factory --port 8000
# tables are created automatically on startup; check http://localhost:8000/api/health

# 3. Frontend (second terminal)
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev                      # http://localhost:3000
```

Without uv: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,ai]"`.

**AI models.** On first use the backend downloads the TripoSR weights (~1.7 GB, Hugging Face `stabilityai/TripoSR`,
MIT) and the IS-Net background-removal model (~180 MB, via rembg). Both are cached (`~/.cache/huggingface`,
`~/.rembg`). Set `RECONSTRUCTION_ALLOW_DOWNLOAD=false` to require pre-downloaded weights, or
`RECONSTRUCTION_BACKEND=none` / `SEGMENTATION_MODEL=none` to disable the AI parts. On an Apple M4 a reconstruction
takes ~12 s (Apple GPU, ~25 s on CPU) plus ~4 s to repair and align the mesh; reconstructions are cached per job.

## Docker

```bash
docker compose up --build
```

Starts PostgreSQL, the API on `http://localhost:8000` and the web app on `http://localhost:3000`. The backend
image includes CPU PyTorch for the AI features (`--build-arg INSTALL_AI=false` for a small image); model weights
are downloaded into the `puzzledata` volume on first use. Give Docker at least 8 GB of memory for AI 3D.
Uploads and generated files live in the `puzzledata` volume, the database in `pgdata`.
`NEXT_PUBLIC_API_URL` is a build argument of the frontend image because the browser calls the API directly.

## Environment variables

See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://localhost:5432/puzzlecut` | SQLAlchemy URL |
| `STORAGE_DIR` | `backend/data` | Root for `uploads/<job_id>/` and `generated/<job_id>/` |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Origins allowed to call the API |
| `MAX_UPLOAD_MB` | `15` | Upload size limit |
| `MAX_IMAGE_SIDE_PX` / `MIN_IMAGE_SIDE_PX` | `8192` / `64` | Hard resolution limits |
| `RECOMMENDED_IMAGE_SIDE_PX` | `4096` | Larger images are accepted with a warning and downscaled |
| `WORKER_THREADS` | `2` | Generation worker threads |
| `LOG_LEVEL` | `INFO` | Backend log level |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API base URL used by the browser |

## Testing

```bash
# Backend: ~380 tests (unit + integration + structural + real-3D + mesh usage + solid slice), AI models off. Uses SQLite in a temp dir by default…
cd backend && .venv/bin/python -m pytest
# …or run the API integration tests against PostgreSQL:
createdb puzzlecut_test
TEST_DATABASE_URL=postgresql+psycopg://localhost:5432/puzzlecut_test .venv/bin/python -m pytest tests/test_api.py

# Opt-in tests that run the real AI models (needs the weights; ~1 minute):
.venv/bin/python -m pytest -m ai

# Frontend: 54 tests (Vitest + Testing Library), type check, lint, production build
cd frontend && npm test && npm run typecheck && npm run lint && npm run build

# Fidelity harness (measurement only; see backend/fidelity/README.md) and per-input pipeline trace
cd backend && .venv/bin/python -m pytest fidelity/tests -q -o addopts=""
.venv/bin/python -m fidelity.run --out fidelity/reports/<name> && .venv/bin/python -m fidelity.report --dir fidelity/reports/<name>
.venv/bin/python -m fidelity.trace --gt quadruped --out /tmp/trace     # or --image photo.jpg / --model model.stl
```

`tests/test_mesh_usage.py` guards that the fossil kit is driven by the 3D body itself: different meshes (and the
same side outline with a different body) give different ribs, slots and sheets, every rib follows the mesh section
on its plane and stays inside it, and the mesh-driven kit is measurably closer to the body than a kit built from the
outline and depth estimate alone. `PUZZLECUT_CONTEXT.md` is the project's working context (decisions, known
problems, next steps).

What the backend tests inspect (not just status codes): image loading/validation, segmentation IoU against known
masks, normalization frame, depth field properties, slot widths **measured** on the generated polygons (exactly
`T + clearance`), kerf compensation (tool path grows by kerf, slots shrink by kerf, simulated cut restores nominal),
3D interpenetration of every rib pair, connectivity, label placement, polygon validity/repair, collision detection,
nesting (no overlap, spacing, bounds, determinism, no-fit error), SVG/DXF/PDF re-parsed and compared with the
tool paths, assembly guide contents, metadata, and full HTTP flows including error cases and path-traversal attempts.
Fixtures: circle, rectangle, animal, irregular, thin parts, concave × 3/4/6 mm × 6/12/20 pieces, plus detailed
side views of a horse, lion, wolf, deer, elephant and eagle, a logo and an irregular object (`tests/animals.py`)
for anatomy, depth, detail-level, adaptive-density, leg, thin-feature, slot-quality and preview/SVG/DXF consistency tests.

## API

All errors use one shape and never include stack traces:

```json
{ "success": false, "error": { "code": "PIECES_DO_NOT_FIT", "message": "These pieces do not fit on the selected sheet. Increase the sheet size or reduce the puzzle size." } }
```

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Service + database status |
| POST | `/api/jobs` | Create a job → `JobOut` |
| POST | `/api/jobs/{id}/upload` | Multipart `file` (PNG/JPG/WEBP ≤ 15 MB). Validates, stores and extracts the silhouette |
| POST | `/api/jobs/{id}/silhouette` | `{"method": "auto"\|"alpha"\|"background"\|"grabcut"\|"threshold"\|"edges"}` re-runs detection |
| POST | `/api/jobs/{id}/analyze` | Re-runs image suitability + structure analysis → `JobOut.suitability` (incl. the editable `skeleton`) |
| GET | `/api/jobs/{id}/structure` | PNG: detected spine (head / neck / torso / tail) and limb axes over the photo |
| POST | `/api/jobs/{id}/generate` | Puzzle settings (below) → `202`, generation runs in the background |
| GET | `/api/jobs/{id}` | Status, stage, progress, silhouette info, settings, result, warnings, error |
| GET | `/api/jobs/{id}/original` · `/silhouette` | Display copy of the upload · white-on-black mask |
| GET | `/api/jobs/{id}/preview` | `preview.png` (rendered assembled model) |
| GET | `/api/jobs/{id}/model` · `/metadata` · `/sheet.svg` | 3D viewer data · `metadata.json` · sheet SVG inline |
| GET | `/api/jobs/{id}/download/svg` · `dxf` · `pdf` · `assembly-guide` | Attachments |

Generate body (all fields optional, defaults shown):

```json
{
  "material": "plywood", "custom_material_name": null,
  "thickness_mm": 3, "kerf_mm": 0.15, "clearance_mm": 0.10,
  "puzzle_size": "medium", "custom_size_mm": null,
  "piece_count": 12, "complexity": "medium", "orientation": "auto", "labels": true,
  "sheet_width_mm": 600, "sheet_height_mm": 400,
  "outputs": ["svg", "dxf", "pdf", "assembly_guide"],
  "shape_source": "auto", "style": "solid",
  "rib_count": 0, "kit_frame": false, "kit_title": null, "override_suitability": false,
  "skeleton": null
}
```

Sizes: small 150 mm, medium 250 mm, large 400 mm (longest side of the object), or custom 40–2000 mm.
Complexity sets the structural detail (see below): easy = spine + cross ribs (one side-profile pair from 12 pieces),
medium = + side profiles that carry the legs and head plates + ribs oriented along necks/tails, advanced = two
side-profile levels + detail plates + strongly adaptive rib density. `style` is `kit` (procedural fossil kit,
see below; the web UI default), `solid` (filled egg-crate) or `skeleton` (egg-crate with windows). For kits,
`piece_count` is a target, `rib_count` 0 = automatic, `kit_frame` adds the frame, `kit_title` is the engraved name
(default: the file name). A generate request for an image judged unsuitable returns `422 IMAGE_UNSUITABLE` with
guidance unless `override_suitability` is true. The SVG is always produced because the web UI previews the sheet from it. Interactive API docs:
`http://localhost:8000/docs`.

---

## Real 3D: AI reconstruction and uploaded models

A single photo has no depth, so for real objects PuzzleCut asks an image-to-3D model for the shape:

1. **AI background removal** – rembg's IS-Net produces a clean cut-out; it competes with the classical methods and
   is preferred unless its mask is degenerate.
2. **Reconstruction** – TripoSR turns the cut-out into a mesh (marching cubes on its density field).
3. **Repair** – the mesh is voxelised and re-meshed (watertight, single body, smoothed, ≤ 60 k faces). The same
   repair makes uploaded STL/OBJ/GLB/PLY files sliceable even when they are open or self-intersecting.
4. **Alignment** – reconstruction models use their own axis conventions. Of the 24 axis-aligned rotations, the one
   whose projection best matches the photo's silhouette (IoU) defines the frame: x right, y up, z towards the
   viewer. Uploaded models are oriented by convention (glTF/OBJ y-up, STL/PLY z-up) and turned so their longest
   horizontal axis is x.
5. **Quality gates** – if the projection matches the photo poorly (IoU < 0.55), or the model is a thin slab
   (depth < 14 % of its size, typical for drawings and logos), the job falls back to the estimated body and says so.
6. **Slicing** – every piece is an exact plane section of the mesh: the spine is the centre section, side profiles
   are sections at ±z (the first one at the depth where the legs really are), cross ribs are sections on their rib
   planes. A slab of thickness T keeps only the region inside both of its faces, so no piece pokes out of the body.
   Slots, validation, nesting and exports are unchanged.

The **3D shape** setting chooses Automatic (AI when available, with the fallbacks above), AI 3D, or Estimate.
The hidden side of a photographed object is inferred by the AI model; a 3D model upload gives the most exact puzzle.

## How the geometry engine works

Canonical frame: `x` = default slicing axis, `y` = up, `z` = depth. Every piece is a flat polygon in its own plane;
cross ribs always contain the depth axis but may be tilted in the xy plane.

1. **Normalize** (`normalize.py`) – trace the mask, flip to y-up, scale so the longest side equals the puzzle size,
   centre on `y = 0`. Features thinner than `max(0.8·T, 2.5 mm)` are not simply deleted: thin features that matter
   (tails, antennae, antlers, thin legs: ≥ 3× the minimum width long and ≥ 35 % of it wide) are rebuilt along their
   medial axis at the minimum cuttable width; only hairlines and tiny spikes are dropped. The user is warned either way.
2. **Anatomy** (`anatomy.py`) – the torso is the largest blob of a morphological opening; every protrusion is an
   appendage classified by where it attaches and points: **legs** (attached low, pointing down, reaching the ground),
   **head/neck** (dominant appendage at one end, split into neck, head blob and thin **extremities** such as ears or
   antlers), **tail** (thin appendage at the other end), other **extremities** (trunk, wings). The torso is split into
   **chest** (head side) and **belly**. Shapes without legs and a head/tail (logos, objects) stay one region. A pruned
   medial skeleton gives the local axis of every limb; outline curvature is measured along the boundary.
3. **Depth model** (`depth.py`, replaceable via `DepthModel`) – tube inflation `D0 = √(d(2R − d))` (`d` distance to
   the outline, `R` local radius) multiplied by a region factor: chest fullest, torso, belly reduced, neck as a
   transition, head with a muzzle that tapers towards the tip, legs narrow and tapering to paws/hooves, tails tapering
   root→tip, extremities thin. Factors are blended with a normalised Gaussian, so the field is smooth everywhere.
4. **Spine** (`LONG-01`) – the backbone: the full silhouette at `z = 0` (head → neck → torso → tail).
5. **Side profiles** (`LONG-02…`) – torso plates at `±z` where `D ≥ |z| + T/2`, with `z` chosen by area coverage.
   When they exist, the **legs move from the spine to the innermost side profiles** (one leg of each front/back pair
   per side; a single drawn leg goes to both) and are bridged up into the plate, like the side panels of real
   animal kits: the model stands on four legs in 3D and the spine ends at the belly.
6. **Detail plates** (`DETAIL-HEAD-01…`) – region-specific level sets: cheek plates for the head/neck, and on
   advanced every separate volume of a level set (haunch, chest…). They are real pieces that need ≥ 2 load-bearing joints.
7. **Cross ribs** (`CROSS-01…`) – candidates are vertical sections along `x` plus sections perpendicular to the
   skeleton axis in necks, heads, tails and trunks. Each candidate must grip the spine with full walls, have a
   section wide enough for its own slot walls and a slot path that does not run through any unrelated part of the
   spine or of a plate. Candidates are weighted by region (head > neck > tail/legs > chest > torso), outline curvature
   and rate of change of the section, and chosen by **weighted farthest-point sampling**: density follows importance,
   while hard constraints keep every pair of rib footprints `W + wall` apart and stop non-parallel ribs from crossing.
   Each pick is checked against the real geometry (grip, cuts that would sever the spine/plates alone or together).
8. **Joints** – for every long piece at `z` and rib with trace `c + t·d`, the shared interval `[p, q]` on their
   common line is split at `m`. The long piece loses the strip `|s| ≤ W/2` from `m` through its edge on the side the
   rib comes from, the rib loses `|u − z| ≤ W/2` from `m` the other way. Inside every intersection box each piece has
   material on one side of `m` only, so nothing interpenetrates and each rib slides in along its own trace. A joint is
   load-bearing when both pieces keep full walls (≥ `max(T, 3 mm)`) for at least `max(T, 3 mm)` starting at the slot
   bottom.
9. **Cleanup & connectivity** – slivers thinner than the minimum feature are removed (mitred opening keeps slot
   corners exact); every rib needs ≥ 1 load-bearing joint, every plate ≥ 2, the joint graph must be connected.
   Failing plates are dropped and their pieces become ribs; if tilted ribs prevent the requested count, parallel
   ribs are used.
10. **Labels** – the ID (or a short code such as `C07` / `DH01` on small pieces) as single-stroke engraving placed at
    the pole of inaccessibility of the visible face, away from slots.

**Detail levels.** For the horse fixture at 20 pieces: easy 5 joints at 6 pieces → medium 21 → advanced 57-71 joints;
the regression tests assert that joints and plates increase with the level and that the head/neck get a higher rib
density per area than the torso.

**Assembly order** (guide): spine; then cross ribs from the middle of the body outwards, each slid in along its own
trace; any plate not yet placed is slid onto the first rib it meets right after that rib. That order always respects
every slot, because each rib only ever slides into plates that are already held.

**Geometry quality** (`quality.py`, shown in the UI and the guide): pieces, slots, intersections and load-bearing
joints, narrowest feature of any piece, thinnest slot wall in the gripping zone, closest approach of two slots,
closest distance between cut paths on the sheet, material area, nesting efficiency and waste.

**Skeleton style** (`style: "skeleton"`). The model looks like a laser-cut fossil kit:
- the spine keeps a backbone band under the top of the torso (head, neck and tail stay whole), and the belly is
  opened. If the legs still hang from the spine, solid columns are kept above them;
- cross ribs span the full body section, so they hang below the backbone as a rib cage;
- side profiles shrink to hip/shoulder plates that carry the legs;
- every piece large enough gets rounded windows inside an outline band. The full length of each slot and its walls
  is protected, so the joints grip exactly as well as in the solid style.

The generator validates the result itself. If an opening ever weakens a joint, it first drops the backbone cut and
then the windows, so it never ships an invalid model. `structure.skeleton` lists which steps were applied.

### Slot sizing

```
nominal (as-cut) slot width  W = T + clearance        e.g. 3.00 + 0.10 = 3.10 mm
slot depth                       to the midpoint of the ribs' shared interval (both slots meet there)
```

Slot widths are verified in the tests by measuring the gap in the generated polygons.

### Kerf compensation

The laser removes `kerf` of material centred on its path. Exported cut contours are the nominal outline **offset
outward from the material by kerf/2** (Shapely mitred buffer):

| feature | in the file | after cutting |
|---|---|---|
| outer contour | +kerf/2 per side | nominal size |
| slot width | `W − kerf` (3.10 − 0.15 = 2.95 mm) | `W` = 3.10 mm |
| slot bottom | shifted kerf/2 into the slot | nominal depth |
| holes | −kerf/2 per side | nominal size |

So compensation is *outward* for outer contours and *inward* for slots and holes, with the same rule
(offset away from material). Engraving is never compensated. `metadata.json` records both slot widths.

**Calibrate:** cut one joint first. Too tight → increase clearance (or kerf); too loose → decrease them.

### SVG / DXF / PDF

- **SVG** – `width="600mm" height="400mm" viewBox="0 0 600 400"` (1 unit = 1 mm), no transforms, no raster images.
  Layer `CUT` (red `#FF0000`, 0.1 mm hairline): one closed path per piece (outline + holes, even-odd). Layer `ENGRAVE`
  (blue `#0000FF`): label strokes as open paths.
- **DXF** – written with ezdxf (R2010, `$INSUNITS` = mm). Layers `CUT` (ACI 1) with closed `LWPOLYLINE`s and `ENGRAVE`
  (ACI 5) with open `LWPOLYLINE`s. The file is re-read and audited before it is accepted.
- **PDF** – page size = sheet size at 1:1, same colours.
- **Nesting** – deterministic raster bottom-left fill (rotations 0/90/180/270°, filled from the top-left corner)
  with exact Shapely verification of spacing (default 3 mm) and sheet margin (5 mm). If anything does not fit:
  *"These pieces do not fit on the selected sheet. Increase the sheet size or reduce the puzzle size."*

### Validation (before anything is written)

Per piece: valid single polygon, closed rings, no self-intersections, zero-length or duplicate segments, no tiny
fragments, positive dimensions, every slot channel free of material, labels inside the piece, narrowest feature ≥ the
minimum, walls beside every load-bearing slot ≥ the minimum, no overlapping slots. Assembly: no long-piece/rib pair
interpenetrates in 3D (1 µm tolerance for the 0.5 µm coordinate grid), rib footprints never touch, parallel plates
don't collide, exact piece count. Tool paths: valid after
kerf offset. Layout: inside the sheet margin, no overlaps, spacing respected. Safe repair is limited to snap
rounding and duplicate removal; anything else fails the job with `INVALID_GEOMETRY`.

---

## Solid-slice style (default) and kit styles

The default style, **Solid slice** (`app/geometry/kit/slice.py`), builds a solid-looking 3D object from one photo or one
3D model: a body-shaped spine plate, dense solid cross-section slices (torso, neck and tail, as wide as the body at
each position, from the belly to the top of the back), limb plates cut from the limb outlines in the image, and skull
plates. The number of slices follows the object's length, the **Detail** setting and what the material allows; the
**Object depth** slider scales the estimated (or reconstructed) depth and really changes slice widths, skull and limb
depth and slot positions. The skeleton kit (below) and the egg-crate styles remain available. See
`PUZZLECUT_CONTEXT.md` §3b.

## Skeleton kit system (`app/geometry/kit/`)

**1. Image suitability** (`vision/suitability.py`, before any geometry). Every value is an *estimated* confidence
from a documented heuristic (no trained model): resolution, segmentation score, Lab colour separation from a k-means
background model of the image border, other foreground blobs of comparable size (multiple subjects), share of the
outline on the image border (subject cut off), subject size, silhouette elongation (side view) and the structure
confidence below. Blocking problems (no subject, several subjects, subject cut off) make `suitable = false` with
guidance; the user can override.

**2. StructureAnalyzer → AnimalStructure** (`structure.py`). The axial body is the silhouette without limbs and
large appendages; its medial skeleton's longest path (double BFS), smoothed and extended to the tips, is the **spine
curve** from the tail end (s = 0) to the head end. Along it: tangent, dorsal normal, distance to the dorsal / ventral
outline and the half-depth from the pluggable `DepthModel` (or the exact depth of a real 3D mesh). Head, neck, torso
and tail are arc-length ranges; the head polygon is the skull blob plus nearby parts (an open jaw) with small
openings closed. Limbs / wings / fins carry their own axis, attachment station and left/right pairing. Nothing
assumes a species, a number of legs, or that a head exists.

**2b. Skeleton editor.** The detected skeleton is returned with the upload (`JobOut.suitability.skeleton`, in
fractions of the photo: spine control points tail -> head, where the head starts, and every limb's joint chain
hip -> knee -> ankle -> toe). In the **Structure** tab the user drags points, adds or removes limbs and toggles the
head; the edited skeleton is sent as `settings.skeleton` and replaces the detection (spine re-fitted through the
points with a spline, head = outline past the head-start cut, limbs = outline around each joint chain).
`ImageFrame` maps photo fractions <-> mm. Detection itself aims the head end of the spine at the far point of the
skull (snout / beak), not an ear or horn.

**3. Designed bone shapes** (`bones.py`, `BoneLibrary`). Pieces are not cut out of the outline or drawn as boxes:
every bone *type* has a designed, parametric profile fitted to the measured skeleton - long bones with round joint
knobs and a narrowing shaft, a foot with splayed toes, a pelvis / scapula blade, tapered ribs whose tips curl
inwards, vertebrae with a tapered neural spine and angled transverse processes, a backbone with vertebral bumps,
oval lightening holes where a bone is wide enough to keep full walls, and Chaikin-smoothed outlines. These are
templates per bone type, never per animal.

**3b. Components** (all parameterised by `ManufacturingConstraints`, derived from thickness, kerf and clearance):

| Generator | Piece | Geometry |
|---|---|---|
| SpineGenerator | `SP` backbone segments (plane z = 0) | band along the spine curve, split at the tail/torso/neck boundaries (and when longer than the sheet allows), widened into a joint boss at each seam |
| ConnectorGenerator | `C` bowtie keys | butterfly key across a seam of two coplanar plates; each host gets a pocket = key + clearance/2 per side |
| RibGenerator / `BoneLibrary.section_rib` | `R` ribs | **with a 3D body** (AI reconstruction or uploaded model): cut from the mesh's exact cross-section on the rib's own plane - a band just inside the section outline from the crown down both flanks and under the belly, open at the belly, so the rib has the body's real width and shape and never leaves it; a solid piece of the section's shape where the body is too thin for an open band; the template below only as a fallback, clipped to the section (`analysis.kit_volume` records the source of every rib). **Without a 3D body**: superellipse arch (two legs, open bottom) sized from local body height and estimated depth. Crown block with the backbone slot in both cases |
| VertebraGenerator / TailGenerator | `V` vertebrae | centrum block + neural spine + transverse processes along neck and tail, scaling with the local body (tail vertebrae shrink towards the tip); with a 3D body the processes are clipped to the measured section |
| SkullGenerator / JawGenerator | `SK` skull plates, `J` jaws | two side plates at ±z; eye socket and nostril are cut-through holes only where a full wall remains; mouth line under the backbone, tapering gap with interleaved teeth; separate jaw joined at the hinge by a bowtie; falls back to a simpler skull when anything would be too thin |
| LimbGenerator / WingGenerator | `L` limb bones | per limb: pelvis / scapula blade around the girdle crossbar, long bones between the joints and a foot with toes (BoneLibrary), split at the knee / ankle knuckles with a bowtie; with a 3D body each limb group stands at the measured depth of the legs in the mesh, one depth per side (ribs next to the girdle are narrowed to fit between the plates); without one, at ±zL just outside the estimated rib cage |
| SpacerGenerator | `S` spacers | beams joining parallel plates through half-lap slots: skull spacers (skull sides + backbone), a jaw spacer inserted from below, girdle crossbars (backbone + both limb plates) |
| JointGenerator | slots | half-lap slots of width T + clearance meeting at the split; engagement ≥ max(T, 3 mm) on each side |
| FrameGenerator | kit frame (sheet only) | cut band around the nested pieces (part spacing + kerf/2 clearance) with the engraved name |

**4. Builder** (`builder.py`). Each cross piece (rib, vertebra, spacer) is accepted only after the same checks the
final validator runs: the plate slot runs through to the edge without splitting the plate, engagement and slot walls
meet the minimums, the narrowest feature stays above the minimum, it does not cross another cross piece, and it does
not interpenetrate *any* plate in 3D. Failed candidates are moved or shrunk; geometry that cannot be made safe is
simplified (e.g. jaw merged into the skull, thin tail tip shortened) with a warning, or the job fails with a clear
error. Rib and vertebra counts follow the piece-count target and the `rib_count` setting; if the result does not fit
the sheet, the kit is regenerated with fewer ribs and vertebrae (stated in the warnings) before reporting
`PIECES_DO_NOT_FIT`.

**5. Validation** adds, for kits: bowtie fit (same plane, no overlap, clearance/2 gap, pocket walls in both hosts)
and assembly connectivity (every piece reachable through joints and connectors).

**6. Assembly guide**: backbone segments + bowties first, then cross pieces from the middle outwards; every
bowtie-joined sub-assembly (a limb, a skull side with its jaw) is pre-assembled and slid on when its first cross piece
is in place.

## Laser manufacturing assumptions

- Flat sheet stock of uniform thickness (plywood, MDF, acrylic…); the actual thickness should be measured and entered.
- Default kerf 0.15 mm (0.20 mm suggested for acrylic) and clearance 0.10 mm; both depend on machine and material.
- Minimum feature width `max(0.8·T, 2.5 mm)`, minimum wall beside a slot `max(T, 3 mm)`.
- Parts may be rotated on the sheet; wood grain direction is not considered.
- Single sheet per job, 5 mm sheet margin, 3 mm part spacing.

## Geometry limitations (honest)

- A single photo has no real depth. With AI 3D the shape comes from TripoSR, which infers the hidden side and smooths
  fine detail (fur, stripes, thin ears); its quality depends on the photo (one clear object, the whole object in
  frame). Without AI, or for flat artwork, the 3D form is a stylized inflation of the silhouette. An uploaded 3D
  model gives exact results.
- Segmentation is classical computer vision. Plain or transparent backgrounds work best; busy photos or subjects that
  fill the frame give lower confidence (the UI warns and requires confirmation) and may need another detection method.
- Anatomy detection is heuristic. It works for side views of animals (legs reaching the ground, a head or tail at an
  end); unusual poses may be read as a generic object, which still produces a valid but less anatomical structure.
- Thin features are reinforced to the minimum cuttable width (slightly bolder than drawn) or, for hairlines, removed.
- Legs are assigned to the left/right side profiles automatically; a drawing that shows only two legs gets each leg
  on both sides.
- All pieces must fit on one sheet.
- Fossil kits: the structure is *inferred* from one outline. Upright subjects (a perched bird) or heads hidden in the
  body outline (lion mane, elephant) are often not recognised as having a head: the kit then follows the main axis
  without a skull. Limbs drawn merged together become one mirrored limb. Real proportions in depth come from the
  depth model (or the AI 3D mesh) and are estimates.
- Fossil kits from a 3D body: ribs follow the mesh sections, but the backbone, skull and limb bones are flat plates
  at fixed depths (z = 0, ±zs, one depth per limb group and side). A body that leaves the spine plane (a three-quarter-view reconstruction whose
  body runs diagonally in depth, a tail curled sideways) is followed by the ribs, not by the backbone or the tail
  vertebrae. Each limb is one flat plate, so a leg that bends sideways is approximated by one plane.
- Bowtie keys hold coplanar bones in the plane; like real kits, a drop of glue makes them permanent.
- Load-bearing joints are judged geometrically (engagement length); real strength depends on material and fit.

## Third-party models and licences

- TripoSR (MIT, Tripo AI & Stability AI) – vendored in `backend/third_party/TripoSR`; weights from Hugging Face.
- IS-Net / DIS background removal (Apache-2.0) via rembg (MIT). The rembg default model in recent versions
  (BRIA RMBG-2.0) is non-commercial and is deliberately **not** used.

## Security notes

Magic-byte type sniffing (client MIME and filename are ignored), Pillow decompression-bomb guard and resolution
limits, size limit enforced while streaming, filenames sanitized and only kept as metadata, storage keys validated
against a strict pattern and resolved inside the storage root, job IDs must be UUIDs, generated files isolated per job,
the web UI sanitizes the SVG it inlines, and no stack traces are returned to clients. 3D models are identified by
content (STL/OBJ/GLB/PLY signatures), parsed from memory without resolving external references, limited to 50 MB
and 3 million faces, and always re-meshed before use.
