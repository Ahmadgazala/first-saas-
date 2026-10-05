export type JobStatus = "created" | "uploaded" | "queued" | "processing" | "completed" | "failed";

export type Material = "plywood" | "mdf" | "acrylic" | "custom";
export type PuzzleSize = "small" | "medium" | "large" | "custom";
export type Complexity = "easy" | "medium" | "advanced";
export type Orientation = "vertical" | "horizontal" | "auto";
export type OutputKind = "svg" | "dxf" | "pdf" | "assembly_guide";
export type SegmentationMethod = "auto" | "ai" | "alpha" | "background" | "grabcut" | "threshold" | "edges";

export interface PuzzleSettings {
  material: Material;
  custom_material_name: string | null;
  thickness_mm: number;
  kerf_mm: number;
  clearance_mm: number;
  puzzle_size: PuzzleSize;
  custom_size_mm: number | null;
  piece_count: number;
  complexity: Complexity;
  orientation: Orientation;
  labels: boolean;
  sheet_width_mm: number;
  sheet_height_mm: number;
  outputs: OutputKind[];
  shape_source: ShapeSource;
  style: ConstructionStyle;
  /** Multiplies the object's estimated (or reconstructed) depth. 1 = as estimated. */
  depth_scale: number;
  rib_count: number;
  kit_frame: boolean;
  kit_title: string | null;
  override_suitability: boolean;
  skeleton: Skeleton | null;
}

export type ConstructionStyle = "slice" | "solid" | "skeleton" | "kit";

/** Editable skeleton in fractions of the photo (x right, y down). */
export interface Skeleton {
  spine: [number, number][];
  head_start: [number, number] | null;
  limbs: { kind: "limb" | "wing" | "fin" | "appendage"; joints: [number, number][] }[];
}

/** ImageSuitabilityAnalyzer result. All confidences are estimated heuristics, not probabilities. */
export interface Suitability {
  suitable: boolean;
  overall_confidence: number;
  silhouette_confidence: number;
  structure_confidence: number;
  recommended_view: string;
  scores: Record<string, number>;
  warnings: string[];
  blocking: string[];
  guidance: string | null;
  structure: {
    has_head?: boolean;
    limb_count?: number;
    spine?: { length_mm: number; children: { kind: string; length_mm: number }[] };
  } | null;
  skeleton?: Skeleton | null;
  confidence_kind: string;
}

export type ShapeSource = "auto" | "ai_3d" | "silhouette";

export interface ApiErrorBody {
  code: string;
  message: string;
}

export interface FileLink {
  kind: OutputKind;
  filename: string;
  url: string;
  size_bytes: number;
}

export interface JobResult {
  piece_count: number;
  material: string;
  thickness_mm: number;
  kerf_mm: number;
  clearance_mm: number;
  slot_width_nominal_mm: number;
  slot_width_toolpath_mm: number;
  object_width_mm: number;
  object_height_mm: number;
  object_depth_mm: number;
  sheet_width_mm: number;
  sheet_height_mm: number;
  used_area_mm2: number;
  waste_percentage: number;
  orientation: string;
  joint_count: number;
  preview_url: string;
  model_url: string;
  sheet_svg_url: string;
  files: FileLink[];
  quality: QualityMetrics;
  piece_roles: Record<string, number>;
  structure: {
    regions?: Record<string, number>;
    tilted_ribs?: number;
    legs_on_side_profiles?: boolean;
    shape_source?: "estimated" | "mesh";
    style?: ConstructionStyle;
    skeleton?: string[];
    kit?: Record<string, number | boolean>;
    connectors?: { id: string; kind: string; pieces: string[] }[];
  };
}

export interface QualityMetrics {
  piece_count?: number;
  profile_pieces?: number;
  cross_pieces?: number;
  slot_count?: number;
  intersection_count?: number;
  load_bearing_joints?: number;
  min_feature_mm?: number;
  min_feature_piece?: string;
  required_min_feature_mm?: number;
  min_slot_wall_mm?: number | null;
  required_min_wall_mm?: number;
  min_slot_gap_mm?: number | null;
  min_cut_distance_mm?: number | null;
  object_width_mm?: number;
  object_height_mm?: number;
  object_depth_mm?: number;
  material_area_mm2?: number;
  used_area_mm2?: number;
  sheet_area_mm2?: number;
  nesting_efficiency_percent?: number;
  waste_percentage?: number;
  tilted_ribs?: number;
}

export interface Job {
  success: true;
  id: string;
  status: JobStatus;
  source_kind: "image" | "model";
  ai_3d_available: boolean;
  stage: string | null;
  progress: number;
  created_at: string;
  updated_at: string;
  image: { filename: string | null; format: string | null; width: number | null; height: number | null; url: string | null } | null;
  silhouette: {
    url: string;
    method: string | null;
    confidence: number | null;
    low_confidence: boolean;
    warnings: string[];
  } | null;
  settings: PuzzleSettings | null;
  result: JobResult | null;
  warnings: string[];
  error: ApiErrorBody | null;
  suitability?: Suitability | null;
  structure_url?: string | null;
}

export interface ModelPiece {
  id: string;
  label: string;
  kind: "profile" | "cross";
  role: "spine" | "profile" | "detail" | "cross" | "rib" | "vertebra" | "skull" | "jaw" | "limb" | "spacer" | "connector";
  style?: string;
  region: string;
  outer: [number, number][];
  holes: [number, number][][];
  engrave: [number, number][][];
  transform: { origin: number[]; u: number[]; v: number[]; n: number[] };
  explode_offset: number[];
  insertion: number[] | null;
  width_mm: number;
  height_mm: number;
  area_mm2: number;
  slots: number;
  connected_to: string[];
  sheet: { rotation_deg: number; bbox_mm: number[] } | null;
}

export interface PuzzleModelData {
  thickness_mm: number;
  object_size_mm: { width: number; height: number; depth: number };
  sheet_mm: { width: number; height: number } | null;
  pieces: ModelPiece[];
  joints: { id: string; profile: string; cross: string; split_mm: number; engagement_mm: number; load_bearing: boolean }[];
}
