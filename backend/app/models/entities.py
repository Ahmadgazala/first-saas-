"""ORM entities. Authentication is optional for the MVP: jobs belong to a Project whose
user_id may be NULL (anonymous). Adding auth later only means populating User/Project."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    CREATED = "created"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    projects: Mapped[list[Project]] = relationship(back_populates="user")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="Untitled puzzle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user: Mapped[User | None] = relationship(back_populates="projects")
    jobs: Mapped[list[PuzzleJob]] = relationship(back_populates="project")


class PuzzleJob(Base):
    __tablename__ = "puzzle_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.CREATED, index=True)
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)

    original_filename: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # "image" (photo -> silhouette -> AI 3D or estimated body) or "model" (uploaded STL/OBJ/GLB/PLY)
    source_kind: Mapped[str] = mapped_column(String(10), default="image", server_default="image")
    image_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_preview_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    silhouette_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segmentation_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    segmentation_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    segmentation_warnings: Mapped[list] = mapped_column(JSON, default=list)
    segmentation_candidates: Mapped[list] = mapped_column(JSON, default=list)
    suitability: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # ImageSuitabilityAnalyzer result

    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # metadata.json content
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="jobs")
    settings: Mapped[PuzzleSettings | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    files: Mapped[list[GeneratedFile]] = relationship(back_populates="job", cascade="all, delete-orphan")


class PuzzleSettings(Base):
    __tablename__ = "puzzle_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("puzzle_jobs.id", ondelete="CASCADE"), unique=True)
    material: Mapped[str] = mapped_column(String(60))
    thickness_mm: Mapped[float] = mapped_column(Float)
    kerf_mm: Mapped[float] = mapped_column(Float)
    clearance_mm: Mapped[float] = mapped_column(Float)
    puzzle_size_preset: Mapped[str] = mapped_column(String(10), default="custom")
    puzzle_size_mm: Mapped[float] = mapped_column(Float)
    piece_count: Mapped[int] = mapped_column(Integer)
    complexity: Mapped[str] = mapped_column(String(20))
    orientation: Mapped[str] = mapped_column(String(20))
    labels: Mapped[bool] = mapped_column(Boolean, default=True)
    sheet_width_mm: Mapped[float] = mapped_column(Float)
    sheet_height_mm: Mapped[float] = mapped_column(Float)
    outputs: Mapped[list] = mapped_column(JSON, default=list)
    shape_source: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    style: Mapped[str] = mapped_column(String(20), default="solid", server_default="solid")
    depth_scale: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    rib_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    kit_frame: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    kit_title: Mapped[str | None] = mapped_column(String(40), nullable=True)
    override_suitability: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    skeleton: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    job: Mapped[PuzzleJob] = relationship(back_populates="settings")


class GeneratedFile(Base):
    __tablename__ = "generated_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("puzzle_jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(80))
    content_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    job: Mapped[PuzzleJob] = relationship(back_populates="files")
