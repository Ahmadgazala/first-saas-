"""ManufacturingConstraints: every size limit the kit generators respect, derived from the
material settings (never hard-coded to one thickness)."""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.types import PuzzleParameters


@dataclass(frozen=True)
class ManufacturingConstraints:
    thickness: float
    kerf: float
    clearance: float
    slot_width: float  # nominal slot width = thickness + clearance
    min_wall_thickness: float  # material beside a slot / around a pocket
    min_gap: float  # narrowest opening cut inside a piece (mouth gap, between teeth)
    min_feature_size: float  # narrowest material anywhere in a piece
    min_slot_length: float  # engagement of each half of a half-lap joint
    min_connector_width: float  # wide end of a bowtie connector

    @classmethod
    def from_params(cls, p: PuzzleParameters) -> "ManufacturingConstraints":
        return cls(
            thickness=p.thickness_mm,
            kerf=p.kerf_mm,
            clearance=p.clearance_mm,
            slot_width=p.slot_width_mm,
            min_wall_thickness=p.min_wall_mm,
            min_gap=max(1.5, 0.5 * p.thickness_mm, 4.0 * p.kerf_mm),
            min_feature_size=p.min_feature_mm,
            min_slot_length=p.min_engagement_mm,
            min_connector_width=max(2.2 * p.thickness_mm, 6.0),
        )

    @property
    def crown_half_width(self) -> float:
        """Half width of the solid block that carries a slot in a cross piece."""
        return self.slot_width / 2.0 + self.min_wall_thickness

    @property
    def bar_half_height_min(self) -> float:
        """Thinnest backbone bar that still takes a half-lap slot with walls below it."""
        return max(self.min_slot_length + 1.0, 0.9 * self.min_wall_thickness, 1.2 * self.thickness)
