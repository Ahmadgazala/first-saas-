"""Skeleton construction style: a fossil-kit look (backbone, hanging rib cage, windowed
plates) that must stay exactly as manufacturable as the solid style."""

import numpy as np
import pytest

from app.geometry.sections import line_intervals

from app.geometry.engine import PuzzleGenerator
from app.geometry.normalize import normalize_silhouette
from app.geometry.types import Complexity, PieceRole, PuzzleParameters, Style
from app.validators.geometry_validator import validate_model
from tests.animals import ANIMALS


def _model(name, style, thickness=3.0, pieces=12, complexity="advanced", size=250.0):
    params = PuzzleParameters(thickness_mm=thickness, piece_count=pieces, complexity=Complexity(complexity),
                              target_size_mm=size, style=Style(style))
    sil = normalize_silhouette(ANIMALS[name](), size, params.min_feature_mm)
    return PuzzleGenerator(params).generate(sil.polygon)


def _spine(model):
    return next(p for p in model.pieces if p.role == PieceRole.SPINE)


@pytest.mark.parametrize("name", ["horse", "lion", "wolf", "deer", "elephant", "eagle", "trex", "logo"])
def test_skeleton_models_validate(name):
    model = _model(name, "skeleton")
    report = validate_model(model)
    assert report.ok, report.errors[:3]
    assert len(model.pieces) == 12
    assert model.analysis["style"] == "skeleton"


@pytest.mark.parametrize("name", ["horse", "trex"])
def test_spine_becomes_a_backbone(name):
    solid, skeleton = _model(name, "solid"), _model(name, "skeleton")
    assert "backbone" in skeleton.analysis["skeleton"]
    # Across the middle of the body the skeleton spine is a band, the solid one is full height.
    a, b = _spine(solid).polygon, _spine(skeleton).polygon
    minx, _, maxx, _ = a.bounds
    xs = np.linspace(minx + 0.35 * (maxx - minx), maxx - 0.35 * (maxx - minx), 21)

    def height(poly, x):
        return sum(hi - lo for lo, hi in line_intervals(poly, float(x)))

    ratios = [height(b, x) / height(a, x) for x in xs if height(a, x) > 0]
    assert float(np.median(ratios)) < 0.7


def test_trex_matches_a_fossil_kit_layout():
    model = _model("trex", "skeleton", pieces=16, size=300)
    assert validate_model(model).ok
    assert model.analysis["legs_on_side_profiles"]
    spine = _spine(model).polygon
    ribs = [p for p in model.pieces if p.role == PieceRole.CROSS and p.region in ("torso", "chest", "belly")]
    assert ribs
    # Ribs hang from the backbone: in the torso they reach well below the opened spine.
    deepest = min(r.frame.cy + min(c[1] for c in r.polygon.exterior.coords) for r in ribs)
    assert deepest < spine.bounds[1] + 0.5 * (spine.bounds[3] - spine.bounds[1])
    # Larger ribs and the leg plates are windowed.
    assert any(len(p.polygon.interiors) for p in model.pieces if p.role == PieceRole.CROSS)
    assert any(len(p.polygon.interiors) for p in model.pieces if p.role == PieceRole.PROFILE)


def test_skeleton_steps_back_instead_of_weakening_a_joint():
    model = _model("wolf", "skeleton", thickness=6.0, pieces=8, complexity="medium")
    assert validate_model(model).ok
    assert set(model.analysis["skeleton"]) <= {"backbone", "windows"}


def test_solid_style_is_unchanged_by_default():
    model = _model("horse", "solid")
    assert model.analysis["style"] == "solid" and "skeleton" not in model.analysis
    assert all(not p.polygon.interiors for p in model.pieces if p.role != PieceRole.SPINE)
