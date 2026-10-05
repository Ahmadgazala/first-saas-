import type { PuzzleSettings } from "./types";

export const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;
export const MAX_IMAGE_SIDE = 8192;
export const RECOMMENDED_IMAGE_SIDE = 4096;
export const MIN_IMAGE_SIDE = 64;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const ACCEPTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"];
export const MODEL_EXTENSIONS = [".stl", ".obj", ".glb", ".ply"];
export const MAX_MODEL_UPLOAD_BYTES = 50 * 1024 * 1024;

export function isModelFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return MODEL_EXTENSIONS.some((ext) => name.endsWith(ext));
}

export const THICKNESS_PRESETS = [3, 4, 6, 9];
export const PIECE_PRESETS = [8, 12, 16, 20, 30, 40, 50, 60];
export const SIZE_PRESETS = { small: 150, medium: 250, large: 400 } as const;
export const SHEET_PRESETS: { label: string; w: number; h: number }[] = [
  { label: "300 × 300 mm", w: 300, h: 300 },
  { label: "600 × 400 mm", w: 600, h: 400 },
  { label: "600 × 900 mm", w: 600, h: 900 },
  { label: "1200 × 600 mm", w: 1200, h: 600 },
];
export const MATERIAL_DEFAULTS = {
  plywood: { kerf: 0.15, label: "Plywood" },
  mdf: { kerf: 0.15, label: "MDF" },
  acrylic: { kerf: 0.2, label: "Acrylic" },
  custom: { kerf: 0.15, label: "Custom" },
} as const;

export const DEFAULT_SETTINGS: PuzzleSettings = {
  material: "plywood",
  custom_material_name: null,
  thickness_mm: 3,
  kerf_mm: 0.15,
  clearance_mm: 0.1,
  puzzle_size: "medium",
  custom_size_mm: null,
  piece_count: 40,
  complexity: "advanced",
  orientation: "auto",
  labels: true,
  sheet_width_mm: 600,
  sheet_height_mm: 400,
  outputs: ["svg", "dxf", "pdf", "assembly_guide"],
  shape_source: "auto",
  style: "slice",
  depth_scale: 1,
  rib_count: 0,
  kit_frame: true,
  kit_title: null,
  override_suitability: false,
  skeleton: null,
};

export function slotWidth(s: Pick<PuzzleSettings, "thickness_mm" | "clearance_mm">): number {
  return Math.round((s.thickness_mm + s.clearance_mm) * 1000) / 1000;
}

export function toolpathSlotWidth(s: Pick<PuzzleSettings, "thickness_mm" | "clearance_mm" | "kerf_mm">): number {
  return Math.round((s.thickness_mm + s.clearance_mm - s.kerf_mm) * 1000) / 1000;
}

export function targetSize(s: PuzzleSettings): number | null {
  return s.puzzle_size === "custom" ? s.custom_size_mm : SIZE_PRESETS[s.puzzle_size];
}

/** Client-side validation mirroring the API rules. Returns field -> message. */
export function validateSettings(s: PuzzleSettings): Record<string, string> {
  const e: Record<string, string> = {};
  const num = (v: number | null | undefined) => typeof v === "number" && Number.isFinite(v);
  if (!num(s.thickness_mm) || s.thickness_mm < 0.5 || s.thickness_mm > 25)
    e.thickness_mm = "Thickness must be between 0.5 and 25 mm.";
  if (!num(s.kerf_mm) || s.kerf_mm < 0 || s.kerf_mm > 1) e.kerf_mm = "Kerf must be between 0 and 1 mm.";
  if (!num(s.clearance_mm) || s.clearance_mm < 0 || s.clearance_mm > 1)
    e.clearance_mm = "Clearance must be between 0 and 1 mm.";
  if (!e.kerf_mm && !e.thickness_mm && !e.clearance_mm && s.kerf_mm >= s.thickness_mm + s.clearance_mm)
    e.kerf_mm = "Kerf must be smaller than the slot width.";
  if (!Number.isInteger(s.piece_count) || s.piece_count < 3 || s.piece_count > 60)
    e.piece_count = "Number of pieces must be a whole number between 3 and 60.";
  const size = targetSize(s);
  if (!num(size) || (size as number) < 40 || (size as number) > 2000)
    e.custom_size_mm = "Puzzle size must be between 40 and 2000 mm.";
  else if ((size as number) < 12 * s.thickness_mm)
    e.custom_size_mm = "Puzzle size is too small for this material thickness.";
  for (const key of ["sheet_width_mm", "sheet_height_mm"] as const) {
    if (!num(s[key]) || s[key] < 50 || s[key] > 3000) e[key] = "Sheet sides must be between 50 and 3000 mm.";
  }
  if (s.material === "custom" && !(s.custom_material_name ?? "").trim())
    e.custom_material_name = "Enter a material name.";
  if (!s.outputs.length) e.outputs = "Select at least one output.";
  if (!Number.isFinite(s.depth_scale) || s.depth_scale < 0.3 || s.depth_scale > 3)
    e.depth_scale = "Object depth must be between 0.3 and 3 times the estimate.";
  if (!Number.isInteger(s.rib_count) || s.rib_count < 0 || s.rib_count > 40)
    e.rib_count = "Rib count must be a whole number between 0 (automatic) and 40.";
  return e;
}

export interface FileCheck {
  ok: boolean;
  error?: string;
}

export function checkFileBasics(file: File): FileCheck {
  const ext = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
  if (file.size === 0) return { ok: false, error: "The selected file is empty." };
  if (isModelFile(file)) {
    if (file.size > MAX_MODEL_UPLOAD_BYTES) {
      return { ok: false, error: `The 3D model is ${(file.size / 1048576).toFixed(1)} MB. Maximum is 50 MB.` };
    }
    return { ok: true };
  }
  if (!ACCEPTED_TYPES.includes(file.type) && !ACCEPTED_EXTENSIONS.includes(ext)) {
    return {
      ok: false,
      error: "Unsupported file type. Please upload a PNG, JPG or WEBP image, or an STL, OBJ, GLB or PLY 3D model.",
    };
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return { ok: false, error: `The file is ${(file.size / 1048576).toFixed(1)} MB. Maximum upload size is 15 MB.` };
  }
  return { ok: true };
}

export function checkResolution(width: number, height: number): FileCheck & { warning?: string } {
  if (width < MIN_IMAGE_SIDE || height < MIN_IMAGE_SIDE)
    return { ok: false, error: `The image is too small (${width}×${height}). Minimum is 64×64 px.` };
  if (width > MAX_IMAGE_SIDE || height > MAX_IMAGE_SIDE)
    return { ok: false, error: `The image is too large (${width}×${height}). Maximum is 8192×8192 px.` };
  if (width > RECOMMENDED_IMAGE_SIDE || height > RECOMMENDED_IMAGE_SIDE)
    return { ok: true, warning: "Large image: it will be downscaled for processing (recommended max 4096×4096)." };
  return { ok: true };
}

export const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  starting: "Starting",
  normalizing: "Cleaning up the silhouette",
  generating: "Generating components and joints",
  building_structure: "Building the structural model",
  validating: "Validating geometry",
  nesting: "Nesting pieces on the sheet",
  exporting: "Writing SVG / DXF / PDF",
  rendering: "Rendering preview",
  assembly_guide: "Creating assembly guide",
  reconstructing_3d: "Reconstructing the 3D shape with AI (about 15–40 s)",
  aligning_3d: "Aligning the 3D model with the photo",
  loading_model: "Preparing the 3D model",
  analyzing: "Analyzing the image",
  completed: "Done",
  failed: "Failed",
};

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

/** Styles built by the kit generators (structure analysis, skeleton editor, kit frame). */
export function isKitStyle(style: PuzzleSettings["style"]): boolean {
  return style === "kit" || style === "slice";
}
