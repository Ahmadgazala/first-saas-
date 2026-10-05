# scratchpad/ — historical analysis and debugging scripts

**Not production code.** Nothing in `backend/` or `frontend/` imports anything from this folder, no test or CI job
runs it, and the scripts are kept only as evidence of how the measurements in `docs/known-issues.md` were obtained.

These are copies of throw-away scripts written during the "regression analysis" and "fix" sessions
(2026-10-04 / 05). The originals live in the session scratchpad outside the repository and were not touched.

## What they are

| script | question it answered |
|---|---|
| `ablate.py <case> <estimated\|ai3d> <out.jsonl> [variants]` | ablation: each recent slice change switched on/off (thin appendages, traced head, narrow rule, plate growth, small holes) → pieces, slices, side/front/top IoU |
| `phase_noise.py <case>` | noise floor: shift only the start of the slice sequence by a few mm and watch front/top IoU move (narrow rule on/off) |
| `phase_noise2.py <case>` | the same, with thin appendages on/off |
| `narrow_vs_section.py` | is "spine half-depth at z = 0" a valid proxy for "no slice can be cut here"? compares it with the real section width of slices that were cut |
| `offcentre.py` | shows WHY the proxy fails on meshes: the body is off the z = 0 plane |
| `grow_fail.py <case>` | which `_try_cross` check rejects slice candidates when the spine plate grows vs not |
| `appendage_cost.py <cases…>` | cost of appendage plates + their crossbar on front/top IoU, and the reference half-depth at the appendage |
| `arm_front.py` | the same for the T-Rex arm, piece by piece (plates, each spacer) |
| `head_spacer.py` | skull spacer attempts in head-frame coordinates vs the traced head opening and the mouth seam |
| `hole_trace.py` | the stage at which a traced head opening disappears (inside `_traced_mouth`) and the mouth-gap scan |
| `eye_info.py` | which segmentation stage holds the eye / head-window information; writes annotated head crops |
| `cat_cmp.py` | what actually differs between two slice runs of the cat photo (slice positions, tilts, top-view pixels) |

## How to run (if you really want to)

They were run with `backend/.venv/bin/python` from the **session scratchpad directory** and have local-only
dependencies, so they will not run unmodified from a fresh clone:

* `sys.path` is derived from the script location (`<repo>/backend`), so they must stay one level below the repository root;
* input files that are not in the repository: `trex_ref.png` (the reference T-Rex kit photo), `trex_mask_holes.png`
  (its segmentation), and real photos under `backend/data/uploads/` or the git-ignored `backend/fidelity/photos/` (see
  `backend/fidelity/cases.py`);
* TripoSR reconstructions cached in `backend/fidelity/cache/` (git-ignored, regenerated on first use).

Most of them import the harness in `backend/fidelity/` (`cases`, `run`, `stages`, `assembly`, `metrics`) and the
production generators, and work by subclassing `SliceGenerator` **in memory**; none modifies production code.

## Rules for this folder

* Keep new throw-away analysis here, never in `backend/app/`.
* If a script proves useful repeatedly, promote it into `backend/fidelity/` (with a test) rather than importing
  from here.
* Do not commit generated images, caches or logs (see `.gitignore`).
