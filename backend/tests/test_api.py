"""Integration: HTTP upload -> silhouette -> generation -> validation -> nesting -> exports."""

import io
import json
import re
import xml.etree.ElementTree as ET

import ezdxf
import numpy as np
from PIL import Image
from pypdf import PdfReader
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from tests.fixtures import animal_mask, encode, mask_to_photo, transparent_png

SETTINGS = {
    "material": "plywood",
    "thickness_mm": 3,
    "kerf_mm": 0.15,
    "clearance_mm": 0.1,
    "puzzle_size": "medium",
    "piece_count": 12,
    "complexity": "medium",
    "orientation": "auto",
    "labels": True,
    "sheet_width_mm": 600,
    "sheet_height_mm": 400,
    "style": "solid",  # these tests exercise the egg-crate engine (the API default is the solid-slice kit)
}


def new_job(client) -> str:
    r = client.post("/api/jobs", json={})
    assert r.status_code == 201
    return r.json()["id"]


def upload(client, job_id, data, name="lion.jpg", ctype="image/jpeg"):
    return client.post(f"/api/jobs/{job_id}/upload", files={"file": (name, data, ctype)})


def generated_job(client, settings=None, image=None):
    job_id = new_job(client)
    r = upload(client, job_id, image or encode(mask_to_photo(animal_mask()), "JPEG"))
    assert r.status_code == 200, r.text
    r = client.post(f"/api/jobs/{job_id}/generate", json=settings or SETTINGS)
    assert r.status_code == 202, r.text
    return job_id, client.get(f"/api/jobs/{job_id}").json()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["database"] == "ok"


def test_full_pipeline_produces_valid_manufacturing_files(client):
    job_id, job = generated_job(client)
    assert job["status"] == "completed", job
    res = job["result"]
    assert res["piece_count"] == 12
    assert res["slot_width_nominal_mm"] == 3.1
    assert res["slot_width_toolpath_mm"] == 2.95
    assert 0 < res["waste_percentage"] < 100
    assert {f["kind"] for f in res["files"]} == {"svg", "dxf", "pdf", "assembly_guide"}

    # SVG: real vector paths, mm units, every piece inside the sheet, no overlap.
    r = client.get(f"/api/jobs/{job_id}/download/svg")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    root = ET.fromstring(r.content)
    assert root.get("width") == "600mm" and root.get("height") == "400mm"
    ns = {"s": "http://www.w3.org/2000/svg"}
    polys = []
    for path in root.find("s:g[@id='CUT']", ns).findall("s:path", ns):
        rings = [
            [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+) (-?[\d.]+)", sub)]
            for sub in re.findall(r"M[^M]+", path.get("d"))
        ]
        poly = Polygon(rings[0], rings[1:])
        assert poly.is_valid
        minx, miny, maxx, maxy = poly.bounds
        assert minx >= 0 and miny >= 0 and maxx <= 600 and maxy <= 400
        polys.append(poly)
    assert len(polys) == 12
    tree = STRtree(polys)
    for i, p in enumerate(polys):
        for j in tree.query(p):
            if j != i:
                assert p.distance(polys[j]) >= 3.0 - 1e-3  # part spacing

    # DXF parses and audits clean.
    r = client.get(f"/api/jobs/{job_id}/download/dxf")
    doc = ezdxf.read(io.StringIO(r.content.decode()))
    assert not doc.audit().has_errors
    assert len(doc.modelspace().query("LWPOLYLINE[layer=='CUT']")) >= 12

    # PDFs.
    r = client.get(f"/api/jobs/{job_id}/download/pdf")
    assert r.headers["content-type"] == "application/pdf"
    assert len(PdfReader(io.BytesIO(r.content)).pages) == 1
    r = client.get(f"/api/jobs/{job_id}/download/assembly-guide")
    assert len(PdfReader(io.BytesIO(r.content)).pages) >= 4

    # Preview, 3D model data and metadata.
    r = client.get(f"/api/jobs/{job_id}/preview")
    assert Image.open(io.BytesIO(r.content)).size == (1200, 900)
    model = client.get(f"/api/jobs/{job_id}/model").json()
    assert len(model["pieces"]) == 12 and model["thickness_mm"] == 3
    meta = client.get(f"/api/jobs/{job_id}/metadata").json()
    assert meta["piece_count"] == 12 and meta["validation"]["passed"]

    # Files are isolated under generated/<job_id>/.
    storage = client.app_state.storage
    for name in ["preview.png", "laser_sheet.svg", "laser_sheet.dxf", "laser_sheet.pdf", "assembly_guide.pdf",
                 "metadata.json", "model.json"]:
        assert storage.exists(f"generated/{job_id}/{name}")


def test_silhouette_preview_and_reextraction(client):
    job_id = new_job(client)
    r = upload(client, job_id, transparent_png(animal_mask()), "lion.png", "image/png")
    job = r.json()
    assert job["status"] == "uploaded"
    assert job["silhouette"]["method"] == "alpha"
    assert job["silhouette"]["low_confidence"] is False
    assert job["image"]["filename"] == "lion.png" and job["image"]["width"] == 512
    img = np.asarray(Image.open(io.BytesIO(client.get(job["silhouette"]["url"]).content)))
    assert set(np.unique(img)) <= {0, 255} and img.mean() > 10
    r = client.post(f"/api/jobs/{job_id}/silhouette", json={"method": "threshold"})
    assert r.status_code == 200 and r.json()["silhouette"]["method"] == "threshold"


def test_filename_is_sanitized(client):
    job_id = new_job(client)
    r = upload(client, job_id, encode(mask_to_photo(animal_mask())), "../../etc/<x>.png", "image/png")
    assert r.json()["image"]["filename"] == "x_.png"


def test_rejects_non_image(client):
    job_id = new_job(client)
    r = upload(client, job_id, b"<?php system($_GET['c']); ?>" * 10, "evil.png", "image/png")
    assert r.status_code == 415
    body = r.json()
    assert body == {"success": False, "error": {"code": "UNSUPPORTED_FORMAT", "message": body["error"]["message"]}}


def test_rejects_oversized_upload(client):
    job_id = new_job(client)  # client fixture limits uploads to 2 MB
    r = upload(client, job_id, b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024), "big.png", "image/png")
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_unknown_and_malformed_job_ids(client):
    assert client.get("/api/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
    r = client.get("/api/jobs/..%2F..%2Fetc")
    assert r.status_code == 404 and r.json()["success"] is False
    assert client.get("/api/jobs/not-a-uuid/download/svg").status_code == 404


def test_generate_requires_upload(client):
    job_id = new_job(client)
    r = client.post(f"/api/jobs/{job_id}/generate", json=SETTINGS)
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_STATE_CONFLICT"


def test_download_before_generation(client):
    job_id = new_job(client)
    r = client.get(f"/api/jobs/{job_id}/download/dxf")
    assert r.status_code == 404 and r.json()["error"]["code"] == "FILE_NOT_READY"


def test_invalid_settings_rejected(client):
    job_id = new_job(client)
    upload(client, job_id, encode(mask_to_photo(animal_mask())))
    bad = dict(SETTINGS, kerf_mm=5.0)
    r = client.post(f"/api/jobs/{job_id}/generate", json=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.post(f"/api/jobs/{job_id}/generate", json=dict(SETTINGS, puzzle_size="custom"))
    assert r.status_code == 422
    assert "Traceback" not in r.text


def test_pieces_do_not_fit_is_reported(client):
    job_id, job = generated_job(client, dict(SETTINGS, sheet_width_mm=200, sheet_height_mm=150))
    assert job["status"] == "failed"
    assert job["error"]["code"] == "PIECES_DO_NOT_FIT"
    assert job["error"]["message"] == (
        "These pieces do not fit on the selected sheet. Increase the sheet size or reduce the puzzle size."
    )
    assert job["result"] is None
    assert client.get(f"/api/jobs/{job_id}/download/svg").status_code == 404


def test_too_many_pieces_is_reported(client):
    _, job = generated_job(client, dict(SETTINGS, thickness_mm=9, piece_count=40, puzzle_size="small"))
    assert job["status"] == "failed" and job["error"]["code"] == "TOO_MANY_PIECES"


def test_output_selection_and_custom_material(client):
    settings = dict(SETTINGS, outputs=["svg", "dxf"], material="custom", custom_material_name="Birch 4mm",
                    thickness_mm=4, piece_count=8, puzzle_size="custom", custom_size_mm=220)
    job_id, job = generated_job(client, settings)
    assert job["status"] == "completed", job
    assert {f["kind"] for f in job["result"]["files"]} == {"svg", "dxf"}
    assert job["result"]["material"] == "Birch 4mm"
    assert job["settings"]["custom_size_mm"] == 220
    assert client.get(f"/api/jobs/{job_id}/download/assembly-guide").status_code == 404
    meta = json.loads(client.get(f"/api/jobs/{job_id}/metadata").content)
    assert meta["thickness_mm"] == 4 and max(meta["object_width_mm"], meta["object_height_mm"]) <= 221


def test_reupload_clears_previous_results(client):
    job_id, job = generated_job(client)
    assert job["status"] == "completed"
    upload(client, job_id, encode(mask_to_photo(animal_mask())))
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "uploaded" and job["result"] is None
    assert not client.app_state.storage.exists(f"generated/{job_id}/laser_sheet.svg")


def test_3d_model_upload_is_sliced_exactly(client):
    from tests.meshes import quadruped

    job_id = new_job(client)
    data = quadruped().export(file_type="stl")
    r = upload(client, job_id, data, "horse.stl", "model/stl")
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["source_kind"] == "model"
    assert job["image"]["format"] == "STL"
    assert job["silhouette"]["method"] == "3d-model"
    assert client.get(job["image"]["url"]).status_code == 200  # rendered model preview
    r = client.post(f"/api/jobs/{job_id}/generate", json=dict(SETTINGS, piece_count=14, orientation="vertical"))
    assert r.status_code == 202
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "completed", job
    assert job["result"]["structure"]["shape_source"] == "mesh"
    assert any("exact sections" in w for w in job["warnings"])
    root = ET.fromstring(client.get(f"/api/jobs/{job_id}/download/svg").content)
    assert len(root.find("{http://www.w3.org/2000/svg}g[@id='CUT']")) == 14
    # Photos-only operations are refused for 3D models.
    r = client.post(f"/api/jobs/{job_id}/silhouette", json={"method": "threshold"})
    assert r.status_code == 409


def test_unknown_binary_is_not_mistaken_for_a_model(client):
    job_id = new_job(client)
    r = upload(client, job_id, b"\x00\x01garbage" * 100, "x.stl", "model/stl")
    assert r.status_code == 415


def test_shape_source_setting_round_trips(client):
    job_id, job = generated_job(client, dict(SETTINGS, shape_source="silhouette", piece_count=8))
    assert job["status"] == "completed"
    assert job["settings"]["shape_source"] == "silhouette"
    assert job["result"]["structure"]["shape_source"] == "estimated"
    assert job["ai_3d_available"] is False


def test_ai_3d_requested_but_unavailable_falls_back_with_a_note(client):
    _, job = generated_job(client, dict(SETTINGS, shape_source="ai_3d", piece_count=8))
    assert job["status"] == "completed"
    assert any("not available" in w for w in job["warnings"])


def test_skeleton_style_round_trips(client):
    job_id, job = generated_job(client, dict(SETTINGS, style="skeleton", piece_count=10))
    assert job["status"] == "completed", job
    assert job["settings"]["style"] == "skeleton"
    assert job["result"]["structure"]["style"] == "skeleton"
    assert client.get(f"/api/jobs/{job_id}/metadata").json()["validation"]["passed"]
    r = client.post(f"/api/jobs/{job_id}/generate", json=dict(SETTINGS, style="lace"))
    assert r.status_code == 422


KIT = dict(SETTINGS, style="kit", piece_count=30, complexity="advanced", puzzle_size="custom", custom_size_mm=300,
           kit_frame=True, kit_title="T-Rex")


def _trex_photo():
    from tests.animals import trex

    return encode(mask_to_photo(trex()), "PNG")


def test_analyze_reports_suitability_and_structure(client):
    job_id = new_job(client)
    job = upload(client, job_id, _trex_photo(), "trex.png", "image/png").json()
    suit = job["suitability"]
    assert suit["suitable"] and suit["confidence_kind"] == "estimated"
    assert suit["structure"]["has_head"] and suit["structure"]["limb_count"] >= 2
    r = client.post(f"/api/jobs/{job_id}/analyze")
    assert r.status_code == 200 and r.json()["suitability"]["overall_confidence"] > 0.6
    img = Image.open(io.BytesIO(client.get(r.json()["structure_url"]).content))
    assert img.size[0] > 100


def test_fossil_kit_end_to_end(client):
    job_id = new_job(client)
    upload(client, job_id, _trex_photo(), "trex.png", "image/png")
    r = client.post(f"/api/jobs/{job_id}/generate", json=KIT)
    assert r.status_code == 202, r.text
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "completed", job
    res = job["result"]
    assert res["structure"]["style"] == "kit"
    roles = res["piece_roles"]
    for role in ("spine", "rib", "vertebra", "skull", "limb", "spacer", "connector"):
        assert roles.get(role, 0) >= 1, roles
    assert job["settings"]["kit_frame"] is True and job["settings"]["style"] == "kit"

    # SVG: separate CUT / ENGRAVE layers, the frame is one more closed cut path, names engraved.
    root = ET.fromstring(client.get(f"/api/jobs/{job_id}/download/svg").content)
    ns = {"s": "http://www.w3.org/2000/svg"}
    cut = root.find("s:g[@id='CUT']", ns)
    engrave = root.find("s:g[@id='ENGRAVE']", ns)
    ids = {p.get("id") for p in cut.findall("s:path", ns)}
    assert "FRAME-cut" in ids and len(ids) == res["piece_count"] + 1
    assert engrave.find("s:path[@id='FRAME-label']", ns) is not None
    polys = {}
    for path in cut.findall("s:path", ns):
        rings = [[(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+) (-?[\d.]+)", sub)]
                 for sub in re.findall(r"M[^M]+", path.get("d"))]
        poly = Polygon(rings[0], rings[1:])
        assert poly.is_valid
        polys[path.get("id")] = poly
    frame = polys.pop("FRAME-cut")
    for pid, poly in polys.items():
        assert frame.distance(poly) >= 3.0 - 1e-3, pid  # never touches a piece
        assert Polygon(frame.interiors[0]).contains(poly)

    # DXF parses and has the frame on the CUT layer; the guide and the 3D data are produced.
    doc = ezdxf.read(io.StringIO(client.get(f"/api/jobs/{job_id}/download/dxf").content.decode()))
    assert not doc.audit().has_errors
    assert len(doc.modelspace().query("LWPOLYLINE[layer=='CUT']")) >= res["piece_count"] + 2
    guide = PdfReader(io.BytesIO(client.get(f"/api/jobs/{job_id}/download/assembly-guide").content))
    text = " ".join(page.extract_text() for page in guide.pages)
    assert "bowtie" in text.lower() and "Arch rib" in text
    model = client.get(f"/api/jobs/{job_id}/model").json()
    assert all(p["style"] == "kit" for p in model["pieces"])
    meta = client.get(f"/api/jobs/{job_id}/metadata").json()
    assert meta["validation"]["passed"] and meta["connectors"]
    conn = meta["connectors"][0]
    by_id = {p["id"]: p for p in meta["pieces"]}
    assert set(conn["pieces"]) <= set(by_id[conn["id"]]["connected_to"])


def test_unsuitable_image_is_refused_unless_overridden(client):
    import cv2

    from tests.animals import ANIMALS

    two = np.zeros((768, 1024), np.uint8)
    two[:, :512] = cv2.resize(ANIMALS["horse"](), (512, 768))
    two[:, 512:] = cv2.resize(ANIMALS["wolf"](), (512, 768))
    job_id = new_job(client)
    job = upload(client, job_id, encode(mask_to_photo(two), "PNG"), "two.png", "image/png").json()
    assert job["suitability"]["suitable"] is False
    r = client.post(f"/api/jobs/{job_id}/generate", json=KIT)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "IMAGE_UNSUITABLE" and "side-view" in err["message"]
    r = client.post(f"/api/jobs/{job_id}/generate", json=dict(KIT, override_suitability=True))
    assert r.status_code == 202


def test_edited_skeleton_round_trips_and_is_used(client):
    job_id = new_job(client)
    job = upload(client, job_id, _trex_photo(), "trex.png", "image/png").json()
    sk = job["suitability"]["skeleton"]
    assert len(sk["spine"]) >= 2 and sk["limbs"]
    edited = dict(sk, limbs=sk["limbs"][:1])
    r = client.post(f"/api/jobs/{job_id}/generate", json=dict(KIT, kit_frame=False, skeleton=edited))
    assert r.status_code == 202, r.text
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "completed", job
    assert job["settings"]["skeleton"]["limbs"] == edited["limbs"]
    assert any("edited by hand" in w for w in job["warnings"])
    bad = dict(edited, spine=[[0.5, 0.5]])
    assert client.post(f"/api/jobs/{job_id}/generate", json=dict(KIT, skeleton=bad)).status_code == 422
