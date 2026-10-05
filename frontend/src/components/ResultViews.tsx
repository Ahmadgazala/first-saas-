"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { ApiError, DOWNLOAD_LABELS, api, apiUrl } from "@/lib/api";
import { STAGE_LABELS, formatBytes } from "@/lib/settings";
import type { Job, JobResult, PuzzleModelData, Suitability } from "@/lib/types";

const PuzzleViewer = dynamic(() => import("./PuzzleViewer").then((m) => m.PuzzleViewer), {
  ssr: false,
  loading: () => <div className="h-full animate-pulse rounded-2xl bg-wood-soft" />,
});

/** Loads model.json (the exported pieces with their 3D placement). */
export function usePuzzleModel(url: string | null | undefined) {
  const [state, setState] = useState<{ url: string | null; model: PuzzleModelData | null; error: string | null }>({
    url: null,
    model: null,
    error: null,
  });
  useEffect(() => {
    if (!url) return;
    let alive = true;
    api
      .getModel(url)
      .then((m) => alive && setState({ url, model: m, error: null }))
      .catch((e) =>
        alive && setState({ url, model: null, error: e instanceof ApiError ? e.message : "The 3D model could not be loaded." }),
      );
    return () => {
      alive = false;
    };
  }, [url]);
  const current = state.url === url;
  return { model: current ? state.model : null, error: current ? state.error : null };
}

export function ModelView({
  model,
  error,
  className = "",
  selectedId = null,
  onSelect,
}: {
  model: PuzzleModelData | null;
  error?: string | null;
  className?: string;
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
}) {
  if (error) return <div className={`flex items-center justify-center rounded-2xl border border-line bg-card p-6 text-sm text-err ${className}`}>{error}</div>;
  if (!model) return <div className={`animate-pulse rounded-2xl bg-wood-soft ${className}`} />;
  return <PuzzleViewer model={model} className={className} selectedId={selectedId} onSelect={onSelect} />;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ALLOWED_SVG_TAGS = new Set(["svg", "g", "path", "title"]);

/** Parse the generated sheet SVG and keep only geometry elements (defence in depth). */
export function sanitizeSheetSvg(text: string): SVGSVGElement | null {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const root = doc.documentElement;
  if (doc.querySelector("parsererror") || root.namespaceURI !== SVG_NS || root.localName !== "svg") return null;
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    if (!ALLOWED_SVG_TAGS.has(el.localName)) el.remove();
    else for (const attr of Array.from(el.attributes)) if (attr.name.startsWith("on") || attr.name.includes("href")) el.removeAttribute(attr.name);
  }
  root.removeAttribute("width");
  root.removeAttribute("height");
  return root as unknown as SVGSVGElement;
}

export function SheetView({
  result,
  className = "",
  selectedId = null,
  connected = [],
  onSelect,
}: {
  result: JobResult;
  className?: string;
  selectedId?: string | null;
  connected?: string[];
  onSelect?: (id: string | null) => void;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(0);
  useEffect(() => {
    let alive = true;
    fetch(apiUrl(result.sheet_svg_url), { cache: "no-store" })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((text) => {
        const svg = sanitizeSheetSvg(text);
        if (!alive || !holder.current) return;
        if (!svg) throw new Error("invalid svg");
        svg.setAttribute("class", "h-full w-full");
        svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
        svg.setAttribute("role", "img");
        svg.setAttribute("aria-label", "Nested laser cutting sheet");
        holder.current.replaceChildren(document.importNode(svg, true));
        setLoaded((n) => n + 1);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [result.sheet_svg_url]);

  // Mirror the 3D selection on the sheet: selected piece and the pieces it interlocks with.
  const connectedKey = connected.join(",");
  useEffect(() => {
    const root = holder.current;
    if (!root) return;
    const linked = new Set(connectedKey ? connectedKey.split(",") : []);
    root.querySelectorAll<SVGPathElement>("path[id]").forEach((el) => {
      const pid = el.id.replace(/-(cut|label)$/, "");
      el.classList.toggle("is-selected", pid === selectedId);
      el.classList.toggle("is-connected", linked.has(pid));
      el.classList.toggle("is-dimmed", Boolean(selectedId) && pid !== selectedId && !linked.has(pid));
    });
  }, [selectedId, connectedKey, loaded]);

  return (
    <div className={`flex flex-col rounded-2xl border border-line bg-card p-3 ${className}`}>
      <div className="mb-2 flex items-center justify-between text-xs text-muted">
        <span>
          Laser sheet · {result.sheet_width_mm} × {result.sheet_height_mm} mm{selectedId ? ` · ${selectedId} highlighted` : ""}
        </span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1"><i className="inline-block h-0.5 w-4 bg-[#FF0000]" /> Cut</span>
          <span className="flex items-center gap-1"><i className="inline-block h-0.5 w-4 bg-[#0000FF]" /> Engrave</span>
        </span>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center rounded-lg bg-[#fbfaf7] p-2">
        {failed ? (
          <p className="text-sm text-err">The sheet preview could not be loaded. The download links still work.</p>
        ) : (
          <div
            ref={holder}
            data-testid="sheet-svg"
            onClick={(e) => {
              const path = (e.target as Element).closest?.("path[id]");
              if (!onSelect) return;
              const pid = path ? path.id.replace(/-(cut|label)$/, "") : null;
              onSelect(pid && pid !== selectedId ? pid : null);
            }}
            className="sheet-svg max-h-full w-full cursor-pointer border border-dashed border-black/20 bg-white"
            style={{ aspectRatio: `${result.sheet_width_mm} / ${result.sheet_height_mm}` }}
          />
        )}
      </div>
    </div>
  );
}

/** Image suitability (estimated confidences) with blocking problems, guidance and an override. */
export function SuitabilityPanel({
  suitability,
  override,
  onOverride,
}: {
  suitability: Suitability;
  override: boolean;
  onOverride?: (v: boolean) => void;
}) {
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const ok = suitability.suitable;
  return (
    <div
      className={`rounded-2xl border p-4 ${ok ? "border-ok/30 bg-ok-soft" : "border-warn/40 bg-warn-soft"}`}
      data-testid="suitability-panel"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold">{ok ? "Image suitable for a structural kit" : "This image may not give a good kit"}</p>
        <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${ok ? "bg-ok text-white" : "bg-warn text-white"}`}>
          {pct(suitability.overall_confidence)} estimated
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-3 gap-2 text-xs">
        <div><dt className="text-muted">Silhouette</dt><dd className="font-semibold">{pct(suitability.silhouette_confidence)}</dd></div>
        <div><dt className="text-muted">Structure</dt><dd className="font-semibold">{pct(suitability.structure_confidence)}</dd></div>
        <div><dt className="text-muted">Best view</dt><dd className="font-semibold capitalize">{suitability.recommended_view}</dd></div>
      </dl>
      {suitability.structure && (
        <p className="mt-2 text-xs text-ink-soft">
          Detected: {suitability.structure.has_head ? "head, " : ""}
          {(suitability.structure.spine?.children ?? []).filter((c) => ["neck", "torso", "tail"].includes(c.kind)).map((c) => c.kind).join(", ") || "main axis"}
          {suitability.structure.limb_count ? `, ${suitability.structure.limb_count} limb${suitability.structure.limb_count > 1 ? "s" : ""}` : ""}.
        </p>
      )}
      {[...suitability.blocking, ...suitability.warnings].length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs">
          {[...suitability.blocking, ...suitability.warnings].map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {!ok && suitability.guidance && <p className="mt-2 text-xs font-medium">{suitability.guidance}</p>}
      {!ok && onOverride && (
        <label className="mt-2 flex items-center gap-2 text-xs font-medium">
          <input type="checkbox" checked={override} onChange={(e) => onOverride(e.target.checked)} className="accent-laser" />
          Generate anyway (I accept a possibly poor result)
        </label>
      )}
      <p className="mt-2 text-[11px] text-muted">
        Confidences are estimated from image heuristics. The kit is an estimated structural model, not an accurate 3D
        reconstruction.
      </p>
    </div>
  );
}

const ROLE_LABEL: Record<string, string> = {
  spine: "Backbone",
  profile: "Side profile",
  detail: "Detail plate",
  cross: "Cross rib",
  rib: "Arch rib",
  vertebra: "Vertebra",
  skull: "Skull plate",
  jaw: "Lower jaw",
  limb: "Limb bone",
  spacer: "Spacer",
  connector: "Bowtie connector",
};
const ROLE_ORDER = ["spine", "profile", "detail", "cross", "skull", "jaw", "limb", "rib", "vertebra", "spacer", "connector"];

/** Details of the selected piece, or a clickable index of all pieces. */
export function PieceInspector({
  model,
  selectedId,
  onSelect,
  material,
}: {
  model: PuzzleModelData | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  material?: string;
}) {
  if (!model) return null;
  const piece = model.pieces.find((p) => p.id === selectedId) ?? null;
  const chip = (id: string, tone: "default" | "linked" = "default") => (
    <button
      key={id}
      type="button"
      onClick={() => onSelect(id === selectedId ? null : id)}
      className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] transition ${
        id === selectedId
          ? "border-laser bg-laser text-white"
          : tone === "linked"
            ? "border-[#2563eb]/40 bg-[#2563eb]/10 text-[#1d4ed8] hover:bg-[#2563eb]/20"
            : "border-line bg-card text-ink-soft hover:border-ink/30"
      }`}
    >
      {id}
    </button>
  );
  if (!piece) {
    const groups = ROLE_ORDER
      .map((role) => ({ role, ids: model.pieces.filter((p) => p.role === role).map((p) => p.id) }))
      .filter((g) => g.ids.length);
    return (
      <div className="space-y-2 rounded-2xl border border-line bg-card p-4" data-testid="piece-inspector">
        <p className="text-sm font-semibold">Pieces</p>
        <p className="text-xs text-muted">Click a piece in the 3D view, on the sheet, or below to inspect it.</p>
        {groups.map((g) => (
          <div key={g.role}>
            <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{ROLE_LABEL[g.role]}s ({g.ids.length})</p>
            <div className="flex flex-wrap gap-1">{g.ids.map((id) => chip(id))}</div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="space-y-3 rounded-2xl border border-laser/40 bg-card p-4" data-testid="piece-inspector">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-mono text-base font-bold">{piece.id}</p>
          <p className="text-xs text-muted">
            {ROLE_LABEL[piece.role]} · {piece.region}
            {piece.label && piece.label !== piece.id ? ` · engraved “${piece.label}”` : ""}
          </p>
        </div>
        <button type="button" onClick={() => onSelect(null)} className="rounded-md px-2 py-1 text-xs text-muted hover:bg-black/5">
          Close
        </button>
      </div>
      <dl className="grid grid-cols-3 gap-2 text-xs">
        <div><dt className="text-muted">Size</dt><dd className="font-semibold tabular-nums">{piece.width_mm.toFixed(0)} × {piece.height_mm.toFixed(0)} mm</dd></div>
        <div><dt className="text-muted">Area</dt><dd className="font-semibold tabular-nums">{(piece.area_mm2 / 100).toFixed(1)} cm²</dd></div>
        <div><dt className="text-muted">Slots</dt><dd className="font-semibold tabular-nums">{piece.slots}</dd></div>
        <div><dt className="text-muted">Type</dt><dd className="font-semibold">{ROLE_LABEL[piece.role] ?? piece.role}</dd></div>
        <div><dt className="text-muted">Material</dt><dd className="font-semibold">{material ?? "-"}</dd></div>
        <div><dt className="text-muted">Thickness</dt><dd className="font-semibold tabular-nums">{model.thickness_mm} mm</dd></div>
        {piece.sheet && (
          <div className="col-span-3">
            <dt className="text-muted">On the sheet</dt>
            <dd className="font-semibold tabular-nums">
              at {piece.sheet.bbox_mm[0].toFixed(0)}, {piece.sheet.bbox_mm[1].toFixed(0)} mm · rotated {piece.sheet.rotation_deg}°
            </dd>
          </div>
        )}
      </dl>
      <div>
        <p className="mb-1 text-xs text-muted">Connects to ({piece.connected_to.length})</p>
        <div className="flex flex-wrap gap-1">{piece.connected_to.map((id) => chip(id, "linked"))}</div>
      </div>
    </div>
  );
}

function Check({ ok }: { ok: boolean }) {
  return <span className={`ml-1 text-xs ${ok ? "text-ok" : "text-err"}`}>{ok ? "✓" : "!"}</span>;
}

export function QualityPanel({ result }: { result: JobResult }) {
  const q = result.quality ?? {};
  if (!q.piece_count) return null;
  const roles = result.piece_roles ?? {};
  const mm = (v: number | null | undefined) => (v == null ? "-" : `${v.toFixed(2)} mm`);
  const reqFeature = q.required_min_feature_mm ?? 0;
  return (
    <div className="rounded-2xl border border-line bg-card p-4" data-testid="quality-panel">
      <p className="mb-3 text-sm font-semibold">Geometry quality</p>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat
          label="Pieces"
          value={`${q.piece_count} (${ROLE_ORDER.filter((r) => roles[r]).map((r) => `${roles[r]} ${ROLE_LABEL[r].toLowerCase()}`).join(" · ")})`}
        />
        <Stat label="Slots / intersections" value={`${q.slot_count} / ${q.intersection_count} (${q.load_bearing_joints} load-bearing)`} />
        <Stat label="Narrowest feature" value={<>{mm(q.min_feature_mm)}<Check ok={(q.min_feature_mm ?? 0) >= reqFeature - 0.1} /></>} />
        <Stat label="Thinnest slot wall" value={<>{mm(q.min_slot_wall_mm)}<Check ok={q.min_slot_wall_mm == null || q.min_slot_wall_mm >= reqFeature - 0.05} /></>} />
        <Stat label="Closest cut paths" value={mm(q.min_cut_distance_mm)} />
        <Stat label="Minimum allowed feature" value={mm(reqFeature)} />
        <Stat label="Material area" value={`${((q.material_area_mm2 ?? 0) / 100).toFixed(0)} cm²`} />
        <Stat label="Nesting efficiency" value={`${(q.nesting_efficiency_percent ?? 0).toFixed(1)}%`} />
        <Stat label="Sheet waste" value={`${(q.waste_percentage ?? result.waste_percentage).toFixed(1)}%`} />
      </dl>
    </div>
  );
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-card px-3 py-2.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

export function ResultStats({ result }: { result: JobResult }) {
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      <Stat label="Pieces" value={`${result.piece_count} (${result.joint_count} joints)`} />
      <Stat label="Material" value={`${result.material}, ${result.thickness_mm} mm`} />
      <Stat label="Kerf / clearance" value={`${result.kerf_mm} / ${result.clearance_mm} mm`} />
      <Stat
        label="Object size (W × H × D)"
        value={`${Math.round(result.object_width_mm)} × ${Math.round(result.object_height_mm)} × ${Math.round(result.object_depth_mm)} mm`}
      />
      <Stat label="Sheet size" value={`${result.sheet_width_mm} × ${result.sheet_height_mm} mm`} />
      <Stat label="Used area / waste" value={`${(result.used_area_mm2 / 100).toFixed(0)} cm² · ${result.waste_percentage.toFixed(1)}%`} />
      <Stat label="Slot width (cut)" value={`${result.slot_width_nominal_mm.toFixed(2)} mm`} />
      <Stat label="Slot width (in file)" value={`${result.slot_width_toolpath_mm.toFixed(2)} mm`} />
      <Stat label="Cross-rib orientation" value={result.orientation} />
      <Stat
        label="3D shape"
        value={result.structure?.shape_source === "mesh" ? "Real 3D model (exact sections)" : "Estimated structural model"}
      />
      <Stat
        label="Construction"
        value={result.structure?.style === "slice" ? "Solid slice" : result.structure?.style === "kit" ? "Skeleton kit" : result.structure?.style === "skeleton" ? "Egg-crate open" : "Egg-crate solid"}
      />
    </dl>
  );
}

export function DownloadButtons({ result }: { result: JobResult }) {
  const order = ["svg", "dxf", "pdf", "assembly_guide"];
  const files = [...result.files].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {files.map((f) => (
        <a
          key={f.kind}
          href={apiUrl(f.url)}
          download={f.filename}
          className="group flex items-center justify-between rounded-xl border border-ink bg-ink px-4 py-3 text-sm font-semibold text-white transition hover:bg-ink/90"
        >
          <span>{DOWNLOAD_LABELS[f.kind] ?? f.filename}</span>
          <span className="text-xs font-normal text-white/60 group-hover:text-white/80">{formatBytes(f.size_bytes)}</span>
        </a>
      ))}
    </div>
  );
}

export function GenerationProgress({ job }: { job: Job | null }) {
  if (!job || !["queued", "processing"].includes(job.status)) return null;
  const pct = Math.max(4, job.progress);
  return (
    <div className="rounded-2xl border border-line bg-card p-4" aria-live="polite">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-semibold">{STAGE_LABELS[job.stage ?? "queued"] ?? job.stage}…</span>
        <span className="tabular-nums text-muted">{pct}%</span>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full bg-black/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="laser-sweep relative h-full overflow-hidden rounded-full bg-laser transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Notice({ tone, title, children }: { tone: "warn" | "error" | "info"; title?: string; children: React.ReactNode }) {
  const styles = {
    warn: "border-warn/30 bg-warn-soft text-warn",
    error: "border-err/30 bg-err-soft text-err",
    info: "border-line bg-card text-ink-soft",
  }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`rounded-xl border px-4 py-3 text-sm ${styles}`}>
      {title && <p className="font-semibold">{title}</p>}
      <div className={title ? "mt-0.5" : ""}>{children}</div>
    </div>
  );
}
