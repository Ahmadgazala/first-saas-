import { vi } from "vitest";
import type { Job, JobResult } from "@/lib/types";

/** Make `new Image()` decode instantly with the given natural size (jsdom never loads images). */
export function mockImageDecode(width: number, height: number, fail = false) {
  class FakeImage {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    naturalWidth = width;
    naturalHeight = height;
    set src(_v: string) {
      setTimeout(() => (fail ? this.onerror?.() : this.onload?.()), 0);
    }
  }
  vi.stubGlobal("Image", FakeImage);
}

export function makeFile(name: string, type: string, bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

export const RESULT: JobResult = {
  piece_count: 12,
  material: "Plywood",
  thickness_mm: 3,
  kerf_mm: 0.15,
  clearance_mm: 0.1,
  slot_width_nominal_mm: 3.1,
  slot_width_toolpath_mm: 2.95,
  object_width_mm: 250,
  object_height_mm: 220.5,
  object_depth_mm: 64.1,
  sheet_width_mm: 600,
  sheet_height_mm: 400,
  used_area_mm2: 52000,
  waste_percentage: 78.33,
  orientation: "vertical",
  joint_count: 21,
  preview_url: "/api/jobs/j1/preview",
  model_url: "/api/jobs/j1/model",
  sheet_svg_url: "/api/jobs/j1/sheet.svg",
  quality: {
    piece_count: 12,
    slot_count: 42,
    intersection_count: 21,
    load_bearing_joints: 19,
    min_feature_mm: 2.61,
    required_min_feature_mm: 2.5,
    min_slot_wall_mm: 3.1,
    min_cut_distance_mm: 3.96,
    material_area_mm2: 52000,
    nesting_efficiency_percent: 47.7,
    waste_percentage: 78.33,
  },
  piece_roles: { spine: 1, profile: 2, cross: 9 },
  structure: { tilted_ribs: 2, legs_on_side_profiles: true },
  files: [
    { kind: "svg", filename: "laser_sheet.svg", url: "/api/jobs/j1/download/svg", size_bytes: 30000 },
    { kind: "dxf", filename: "laser_sheet.dxf", url: "/api/jobs/j1/download/dxf", size_bytes: 80000 },
    { kind: "pdf", filename: "laser_sheet.pdf", url: "/api/jobs/j1/download/pdf", size_bytes: 20000 },
    { kind: "assembly_guide", filename: "assembly_guide.pdf", url: "/api/jobs/j1/download/assembly-guide", size_bytes: 1800000 },
  ],
};

export function makeJob(patch: Partial<Job> = {}): Job {
  return {
    success: true,
    id: "j1",
    status: "uploaded",
    source_kind: "image",
    ai_3d_available: true,
    stage: null,
    progress: 0,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    image: { filename: "lion.png", format: "PNG", width: 512, height: 512, url: "/api/jobs/j1/original" },
    silhouette: { url: "/api/jobs/j1/silhouette", method: "background", confidence: 0.92, low_confidence: false, warnings: [] },
    settings: null,
    result: null,
    warnings: [],
    error: null,
    ...patch,
  };
}
