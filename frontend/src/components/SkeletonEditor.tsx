"use client";

import { useRef, useState } from "react";
import type { Skeleton } from "@/lib/types";

type Pt = [number, number];
type Handle = { kind: "spine"; i: number } | { kind: "head" } | { kind: "limb"; limb: number; i: number };

const W = 1000;

function clamp(v: number) {
  return Math.min(1.2, Math.max(-0.2, v));
}

function pointAlong(spine: Pt[], f: number): Pt {
  if (spine.length === 0) return [0.5, 0.5];
  const i = Math.min(spine.length - 1, Math.max(0, Math.round(f * (spine.length - 1))));
  return spine[i];
}

/**
 * Drag the detected skeleton into place before generating: spine control points (tail -> head),
 * where the head starts, and the joint chain of every limb (hip / shoulder -> toe).
 * Coordinates are fractions of the photo (x right, y down), the same as the API.
 */
export function SkeletonEditor({
  imageUrl,
  value,
  edited,
  onChange,
  onReset,
}: {
  imageUrl: string;
  value: Skeleton;
  edited: boolean;
  onChange: (s: Skeleton) => void;
  onReset: () => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [aspect, setAspect] = useState(0.75);
  const [drag, setDrag] = useState<Handle | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const H = W * aspect;

  const toSvg = (p: Pt) => [p[0] * W, p[1] * H] as const;
  const eventPoint = (e: React.PointerEvent): Pt | null => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r || r.width === 0 || r.height === 0) return null;
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };

  const move = (h: Handle, p: Pt) => {
    const next: Skeleton = {
      spine: value.spine.map((q) => [...q] as Pt),
      head_start: value.head_start ? ([...value.head_start] as Pt) : null,
      limbs: value.limbs.map((l) => ({ ...l, joints: l.joints.map((q) => [...q] as Pt) })),
    };
    if (h.kind === "spine") next.spine[h.i] = p;
    else if (h.kind === "head") next.head_start = p;
    else next.limbs[h.limb].joints[h.i] = p;
    onChange(next);
  };

  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = eventPoint(e);
    if (p) move(drag, p);
  };

  const addLimb = () => {
    const hip = pointAlong(value.spine, 0.5);
    const foot = Math.min(1, hip[1] + 0.45);
    const joints: Pt[] = [hip, [hip[0], hip[1] + (foot - hip[1]) * 0.45], [hip[0], hip[1] + (foot - hip[1]) * 0.85], [hip[0] + 0.05, foot]];
    onChange({ ...value, limbs: [...value.limbs, { kind: "limb", joints }] });
    setSelected(value.limbs.length);
  };

  const removeLimb = () => {
    if (selected === null) return;
    onChange({ ...value, limbs: value.limbs.filter((_, i) => i !== selected) });
    setSelected(null);
  };

  const toggleHead = () => {
    onChange({ ...value, head_start: value.head_start ? null : pointAlong(value.spine, 0.85) });
  };

  const r = 9;
  const handle = (h: Handle, p: Pt, color: string, label: string) => {
    const [x, y] = toSvg(p);
    return (
      <circle
        key={label}
        data-testid={`handle-${label}`}
        cx={x}
        cy={y}
        r={r}
        fill={color}
        stroke="white"
        strokeWidth={3}
        style={{ cursor: "grab", touchAction: "none" }}
        onPointerDown={(e) => {
          (e.target as Element).setPointerCapture?.(e.pointerId);
          setDrag(h);
          if (h.kind === "limb") setSelected(h.limb);
        }}
        onPointerUp={() => setDrag(null)}
      >
        <title>{label}</title>
      </circle>
    );
  };

  const line = (pts: Pt[], color: string, width: number, key: string, onClick?: () => void) => (
    <polyline
      key={key}
      points={pts.map((p) => toSvg(p).join(",")).join(" ")}
      fill="none"
      stroke={color}
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      onClick={onClick}
      style={onClick ? { cursor: "pointer" } : undefined}
    />
  );

  return (
    <div className="flex h-full select-none flex-col gap-2" data-testid="skeleton-editor">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <button type="button" onClick={addLimb} className="rounded-lg border border-line bg-card px-2.5 py-1 font-medium hover:border-ink/40">
          + Add limb
        </button>
        <button
          type="button"
          onClick={removeLimb}
          disabled={selected === null}
          className="rounded-lg border border-line bg-card px-2.5 py-1 font-medium hover:border-ink/40 disabled:opacity-40"
        >
          Remove limb
        </button>
        <button type="button" onClick={toggleHead} aria-pressed={Boolean(value.head_start)} className="rounded-lg border border-line bg-card px-2.5 py-1 font-medium hover:border-ink/40">
          {value.head_start ? "No head" : "Add head"}
        </button>
        <button type="button" onClick={onReset} disabled={!edited} className="rounded-lg border border-line bg-card px-2.5 py-1 font-medium hover:border-ink/40 disabled:opacity-40">
          Reset to detected
        </button>
        <span className={`ml-auto ${edited ? "font-medium text-laser" : "text-muted"}`}>
          {edited ? "Edited: the kit will follow your skeleton" : "Drag the points to correct the skeleton"}
        </span>
      </div>
      <div className="relative min-h-0 flex-1">
        <div className="relative mx-auto h-full" style={{ aspectRatio: `${1 / aspect}` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Photo with the editable skeleton"
            draggable={false}
            className="absolute inset-0 h-full w-full object-fill opacity-80"
            onLoad={(e) => {
              const img = e.currentTarget;
              if (img.naturalWidth) setAspect(img.naturalHeight / img.naturalWidth);
            }}
          />
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="absolute inset-0 h-full w-full"
            onPointerMove={onMove}
            onPointerUp={() => setDrag(null)}
            onPointerLeave={() => setDrag(null)}
            role="img"
            aria-label="Editable skeleton"
          >
            {line(value.spine, "#10b981", 6, "spine")}
            {value.limbs.map((l, li) =>
              line(l.joints, selected === li ? "#ea4f1d" : "#0ea5e9", selected === li ? 7 : 5, `limb-${li}`, () => setSelected(li)),
            )}
            {value.spine.map((p, i) => handle({ kind: "spine", i }, p, "#10b981", `spine-${i}`))}
            {value.limbs.flatMap((l, li) =>
              l.joints.map((p, i) => handle({ kind: "limb", limb: li, i }, p, selected === li ? "#ea4f1d" : "#0ea5e9", `limb-${li}-${i}`)),
            )}
            {value.head_start && handle({ kind: "head" }, value.head_start, "#ef4444", "head")}
          </svg>
        </div>
      </div>
      <p className="text-[11px] text-muted">
        <span className="text-[#10b981]">●</span> spine (tail → head) · <span className="text-[#ef4444]">●</span> where the head starts ·{" "}
        <span className="text-[#0ea5e9]">●</span> limb joints (hip → knee → ankle → toe). Click a limb to select it.
      </p>
    </div>
  );
}
