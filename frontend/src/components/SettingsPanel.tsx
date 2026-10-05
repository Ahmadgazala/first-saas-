"use client";

import { useState } from "react";
import {
  MATERIAL_DEFAULTS,
  PIECE_PRESETS,
  SHEET_PRESETS,
  SIZE_PRESETS,
  THICKNESS_PRESETS,
  isKitStyle,
  slotWidth,
  toolpathSlotWidth,
} from "@/lib/settings";
import type { Complexity, ConstructionStyle, Material, Orientation, OutputKind, PuzzleSettings, PuzzleSize } from "@/lib/types";

interface Props {
  value: PuzzleSettings;
  onChange: (next: PuzzleSettings) => void;
  errors: Record<string, string>;
  disabled?: boolean;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <fieldset className="border-t border-line pt-4 first:border-t-0 first:pt-0">
      <legend className="mb-2 flex w-full items-baseline justify-between text-sm font-semibold">
        <span>{title}</span>
        {hint && <span className="text-xs font-normal text-muted">{hint}</span>}
      </legend>
      {children}
    </fieldset>
  );
}

function Chips<T extends string | number>({
  options,
  value,
  onSelect,
  label,
  disabled,
  render = (o) => String(o),
}: {
  options: readonly T[];
  value: T | "custom";
  onSelect: (v: T | "custom") => void;
  label: string;
  disabled?: boolean;
  render?: (o: T | "custom") => string;
}) {
  const all = [...options, "custom" as const];
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1.5">
      {all.map((o) => {
        const active = o === value;
        return (
          <button
            key={String(o)}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onSelect(o)}
            className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 ${
              active ? "border-ink bg-ink text-white" : "border-line bg-card text-ink-soft hover:border-ink/40"
            }`}
          >
            {o === "custom" ? "Custom" : render(o)}
          </button>
        );
      })}
    </div>
  );
}

function NumberField({
  id,
  label,
  value,
  onChange,
  step = 0.01,
  unit = "mm",
  error,
  disabled,
}: {
  id: string;
  label: string;
  value: number | null;
  onChange: (v: number) => void;
  step?: number;
  unit?: string;
  error?: string;
  disabled?: boolean;
}) {
  return (
    <label htmlFor={id} className="block">
      <span className="mb-1 block text-xs font-medium text-ink-soft">{label}</span>
      <span className="relative block">
        <input
          id={id}
          type="number"
          inputMode="decimal"
          step={step}
          value={value ?? ""}
          disabled={disabled}
          aria-label={label}
          aria-invalid={Boolean(error)}
          onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
          className={`w-full rounded-lg border bg-card px-3 py-2 pr-11 text-sm tabular-nums outline-none focus:ring-2 focus:ring-laser/30 disabled:opacity-60 ${
            error ? "border-err" : "border-line focus:border-laser"
          }`}
        />
        <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted">{unit}</span>
      </span>
      {error && <span className="mt-1 block text-xs text-err">{error}</span>}
    </label>
  );
}

const OUTPUTS: { kind: OutputKind; label: string }[] = [
  { kind: "svg", label: "SVG" },
  { kind: "dxf", label: "DXF" },
  { kind: "pdf", label: "PDF" },
  { kind: "assembly_guide", label: "Assembly Guide" },
];

export function SettingsPanel({ value: s, onChange, errors, disabled }: Props) {
  const set = (patch: Partial<PuzzleSettings>) => onChange({ ...s, ...patch });
  const [customThickness, setCustomThickness] = useState(!THICKNESS_PRESETS.includes(s.thickness_mm));
  const [customPieces, setCustomPieces] = useState(!PIECE_PRESETS.includes(s.piece_count));
  const sheetPreset = SHEET_PRESETS.find((p) => p.w === s.sheet_width_mm && p.h === s.sheet_height_mm);
  const [customSheet, setCustomSheet] = useState(!sheetPreset);

  return (
    <div className="space-y-4">
      <Section title="Material">
        <Chips<Exclude<Material, "custom">>
          label="Material"
          options={["plywood", "mdf", "acrylic"]}
          value={s.material}
          disabled={disabled}
          render={(o) => MATERIAL_DEFAULTS[o as Material].label}
          onSelect={(m) => set({ material: m as Material, kerf_mm: MATERIAL_DEFAULTS[m as Material].kerf })}
        />
        {s.material === "custom" && (
          <label className="mt-2 block">
            <span className="mb-1 block text-xs font-medium text-ink-soft">Material name</span>
            <input
              type="text"
              maxLength={60}
              value={s.custom_material_name ?? ""}
              disabled={disabled}
              onChange={(e) => set({ custom_material_name: e.target.value })}
              placeholder="e.g. Birch plywood"
              className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-laser focus:ring-2 focus:ring-laser/30"
            />
            {errors.custom_material_name && <span className="mt-1 block text-xs text-err">{errors.custom_material_name}</span>}
          </label>
        )}
      </Section>

      <Section title="Material thickness" hint={`Slot width ${slotWidth(s).toFixed(2)} mm`}>
        <Chips<number>
          label="Material thickness"
          options={THICKNESS_PRESETS}
          value={customThickness ? "custom" : s.thickness_mm}
          disabled={disabled}
          render={(o) => `${o} mm`}
          onSelect={(t) => {
            setCustomThickness(t === "custom");
            if (t !== "custom") set({ thickness_mm: t });
          }}
        />
        {customThickness && (
          <div className="mt-2">
            <NumberField id="thickness" label="Custom thickness" value={s.thickness_mm} step={0.1}
              onChange={(v) => set({ thickness_mm: v })} error={errors.thickness_mm} disabled={disabled} />
          </div>
        )}
        {!customThickness && errors.thickness_mm && <p className="mt-1 text-xs text-err">{errors.thickness_mm}</p>}
      </Section>

      <Section title="Kerf & clearance" hint={`In file: ${toolpathSlotWidth(s).toFixed(2)} mm slots`}>
        <div className="grid grid-cols-2 gap-3">
          <NumberField id="kerf" label="Kerf (laser cut width)" value={s.kerf_mm} onChange={(v) => set({ kerf_mm: v })}
            error={errors.kerf_mm} disabled={disabled} />
          <NumberField id="clearance" label="Clearance (fit play)" value={s.clearance_mm}
            onChange={(v) => set({ clearance_mm: v })} error={errors.clearance_mm} disabled={disabled} />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Slots are {s.thickness_mm || "T"} + {s.clearance_mm || 0} mm wide after cutting. Contours are offset by
          kerf/2 so cut parts come out at their exact design size.
        </p>
      </Section>

      <Section title="Puzzle size" hint="Longest side of the object">
        <Chips<Exclude<PuzzleSize, "custom">>
          label="Puzzle size"
          options={["small", "medium", "large"]}
          value={s.puzzle_size}
          disabled={disabled}
          render={(o) => `${o[0].toUpperCase()}${o.slice(1)} · ${SIZE_PRESETS[o as keyof typeof SIZE_PRESETS]} mm`}
          onSelect={(v) =>
            set({ puzzle_size: v as PuzzleSize, custom_size_mm: v === "custom" ? (s.custom_size_mm ?? 300) : null })
          }
        />
        {s.puzzle_size === "custom" && (
          <div className="mt-2">
            <NumberField id="size" label="Custom size" value={s.custom_size_mm} step={1}
              onChange={(v) => set({ custom_size_mm: v })} error={errors.custom_size_mm} disabled={disabled} />
          </div>
        )}
        {s.puzzle_size !== "custom" && errors.custom_size_mm && <p className="mt-1 text-xs text-err">{errors.custom_size_mm}</p>}
      </Section>

      <Section title="Number of pieces">
        <Chips<number>
          label="Number of pieces"
          options={PIECE_PRESETS}
          value={customPieces ? "custom" : s.piece_count}
          disabled={disabled}
          onSelect={(n) => {
            setCustomPieces(n === "custom");
            if (n !== "custom") set({ piece_count: n });
          }}
        />
        {customPieces && (
          <div className="mt-2">
            <NumberField id="pieces" label="Custom piece count" value={s.piece_count} step={1} unit="pcs"
              onChange={(v) => set({ piece_count: v })} error={errors.piece_count} disabled={disabled} />
          </div>
        )}
      </Section>

      <Section title={s.style === "slice" ? "Detail" : "Complexity"} hint="Structural detail">
        <div role="radiogroup" aria-label="Complexity" className="grid grid-cols-3 gap-1.5">
          {(
            (s.style === "slice"
              ? [
                  ["easy", "Easy", "Fewer, wider-spaced slices"],
                  ["medium", "Medium", "More slices, skull details"],
                  ["advanced", "Advanced", "Dense slices, all details"],
                ]
              : s.style === "kit"
              ? [
                  ["easy", "Easy", "Skull plates, 2 bones per limb"],
                  ["medium", "Medium", "+ jaw, teeth, 3 bones per limb"],
                  ["advanced", "Advanced", "+ nostrils, vertebra wings"],
                ]
              : [
                  ["easy", "Easy", "Spine + ribs"],
                  ["medium", "Medium", "+ side profiles"],
                  ["advanced", "Advanced", "+ detail plates"],
                ]) as [Complexity, string, string][]
          ).map(([k, label, sub]) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={s.complexity === k}
              disabled={disabled}
              onClick={() => set({ complexity: k })}
              className={`rounded-lg border px-2 py-2 text-left text-sm transition disabled:opacity-50 ${
                s.complexity === k ? "border-ink bg-ink text-white" : "border-line bg-card hover:border-ink/40"
              }`}
            >
              <span className="block font-medium">{label}</span>
              <span className={`block text-xs ${s.complexity === k ? "text-white/70" : "text-muted"}`}>{sub}</span>
            </button>
          ))}
        </div>
      </Section>

      <Section title="Construction style" hint="How the model looks">
        <div role="radiogroup" aria-label="Construction style" className="grid grid-cols-2 gap-1.5">
          {(
            [
              ["slice", "Solid slice", "Body plate + dense solid slices"],
              ["kit", "Skeleton kit", "Vertebrae, arch ribs, skull, limb bones"],
              ["skeleton", "Egg-crate open", "Egg-crate with open windows"],
              ["solid", "Egg-crate solid", "Filled egg-crate sections"],
            ] as [ConstructionStyle, string, string][]
          ).map(([k, label, sub]) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={s.style === k}
              disabled={disabled}
              onClick={() => set({ style: k })}
              className={`rounded-lg border px-2 py-2 text-left text-sm transition disabled:opacity-50 ${
                s.style === k ? "border-ink bg-ink text-white" : "border-line bg-card hover:border-ink/40"
              }`}
            >
              <span className="block font-medium">{label}</span>
              <span className={`block text-xs ${s.style === k ? "text-white/70" : "text-muted"}`}>{sub}</span>
            </button>
          ))}
        </div>
        {s.style === "slice" && (
          <div className="mt-3 rounded-xl border border-line bg-paper p-3" data-testid="depth-control">
            <label className="block text-sm" htmlFor="depth-scale">
              <span className="flex items-baseline justify-between">
                <span className="font-medium">Object depth</span>
                <span className="text-xs text-muted" data-testid="depth-value">×{s.depth_scale.toFixed(2)}</span>
              </span>
              <span className="block text-xs text-muted">
                A single photo cannot show how thick the object is. Slice width, body volume and slot positions follow this.
              </span>
            </label>
            <input
              id="depth-scale"
              type="range"
              aria-label="Object depth"
              min={0.4}
              max={2}
              step={0.05}
              value={s.depth_scale}
              disabled={disabled}
              onChange={(e) => set({ depth_scale: Number(e.target.value) })}
              className="mt-2 w-full accent-laser"
            />
            <div className="flex justify-between text-[11px] text-muted">
              <span>flatter</span>
              <span>as estimated</span>
              <span>thicker</span>
            </div>
            {errors.depth_scale && <p className="mt-1 text-xs text-err">{errors.depth_scale}</p>}
          </div>
        )}
        {isKitStyle(s.style) && (
          <div className="mt-3 space-y-3 rounded-xl border border-line bg-paper p-3" data-testid="kit-options">
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>
                <span className="font-medium">Kit frame</span>
                <span className="block text-xs text-muted">Cut frame around the pieces with the kit name engraved</span>
              </span>
              <input
                type="checkbox"
                aria-label="Kit frame"
                checked={s.kit_frame}
                disabled={disabled}
                onChange={(e) => set({ kit_frame: e.target.checked })}
                className="h-4 w-4 accent-laser"
              />
            </label>
            {s.kit_frame && (
              <label className="block text-sm">
                <span className="mb-1 block text-xs font-medium text-ink-soft">Name on the frame</span>
                <input
                  type="text"
                  aria-label="Kit name"
                  maxLength={40}
                  placeholder="Taken from the file name"
                  value={s.kit_title ?? ""}
                  disabled={disabled}
                  onChange={(e) => set({ kit_title: e.target.value || null })}
                  className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-laser"
                />
              </label>
            )}
            <div>
              <span className="mb-1 block text-xs font-medium text-ink-soft">{s.style === "slice" ? "Slices (maximum)" : "Arch ribs"}</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-pressed={s.rib_count === 0}
                  disabled={disabled}
                  onClick={() => set({ rib_count: 0 })}
                  className={`rounded-lg border px-3 py-1.5 text-sm ${s.rib_count === 0 ? "border-ink bg-ink text-white" : "border-line bg-card"}`}
                >
                  Auto
                </button>
                <input
                  type="number"
                  aria-label="Rib count"
                  min={1}
                  max={40}
                  step={1}
                  value={s.rib_count || ""}
                  placeholder="count"
                  disabled={disabled}
                  onChange={(e) => set({ rib_count: e.target.value === "" ? 0 : Number(e.target.value) })}
                  className="w-24 rounded-lg border border-line bg-card px-3 py-1.5 text-sm outline-none focus:border-laser"
                />
              </div>
              {errors.rib_count && <p className="mt-1 text-xs text-err">{errors.rib_count}</p>}
            </div>
            <p className="text-xs text-muted">
              {s.style === "slice"
                ? "The number of pieces is a maximum: the slice count follows the object, the Detail setting and what fits at this size and thickness."
                : "The number of pieces is a target: the kit follows the detected structure and what fits at this size and thickness."}
            </p>
          </div>
        )}
      </Section>

      <Section title="Sheet size">
        <Chips<string>
          label="Sheet size"
          options={SHEET_PRESETS.map((p) => p.label)}
          value={customSheet ? "custom" : (sheetPreset?.label ?? "custom")}
          disabled={disabled}
          onSelect={(l) => {
            setCustomSheet(l === "custom");
            const p = SHEET_PRESETS.find((x) => x.label === l);
            if (p) set({ sheet_width_mm: p.w, sheet_height_mm: p.h });
          }}
        />
        {customSheet && (
          <div className="mt-2 grid grid-cols-2 gap-3">
            <NumberField id="sheet-w" label="Sheet width" value={s.sheet_width_mm} step={1}
              onChange={(v) => set({ sheet_width_mm: v })} error={errors.sheet_width_mm} disabled={disabled} />
            <NumberField id="sheet-h" label="Sheet height" value={s.sheet_height_mm} step={1}
              onChange={(v) => set({ sheet_height_mm: v })} error={errors.sheet_height_mm} disabled={disabled} />
          </div>
        )}
      </Section>

      <Section
        title="Assembly orientation"
        hint={isKitStyle(s.style) ? "Egg-crate styles only (kits follow the body axis)" : "Direction of the cross ribs"}
      >
        <div role="radiogroup" aria-label="Assembly orientation" className="grid grid-cols-3 gap-1.5">
          {(["auto", "vertical", "horizontal"] as Orientation[]).map((o) => (
            <button
              key={o}
              type="button"
              role="radio"
              aria-checked={s.orientation === o}
              disabled={disabled}
              onClick={() => set({ orientation: o })}
              className={`rounded-lg border px-2 py-1.5 text-sm font-medium transition disabled:opacity-50 ${
                s.orientation === o ? "border-ink bg-ink text-white" : "border-line bg-card text-ink-soft hover:border-ink/40"
              }`}
            >
              {o === "auto" ? "Automatic" : o[0].toUpperCase() + o.slice(1)}
            </button>
          ))}
        </div>
      </Section>

      <Section title="Output">
        <div className="flex items-center justify-between rounded-lg border border-line bg-card px-3 py-2">
          <span className="text-sm">Engrave piece labels (R01, V01, SP01…)</span>
          <button
            type="button"
            role="switch"
            aria-checked={s.labels}
            aria-label="Piece labels"
            disabled={disabled}
            onClick={() => set({ labels: !s.labels })}
            className={`relative h-6 w-11 rounded-full transition ${s.labels ? "bg-laser" : "bg-black/15"}`}
          >
            <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition ${s.labels ? "left-[22px]" : "left-0.5"}`} />
          </button>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {OUTPUTS.map(({ kind, label }) => {
            const checked = s.outputs.includes(kind);
            return (
              <label key={kind} className="flex items-center gap-2 rounded-lg border border-line bg-card px-3 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={() =>
                    set({ outputs: checked ? s.outputs.filter((k) => k !== kind) : [...s.outputs, kind] })
                  }
                  className="accent-laser"
                />
                {label}
              </label>
            );
          })}
        </div>
        {errors.outputs && <p className="mt-1 text-xs text-err">{errors.outputs}</p>}
      </Section>
    </div>
  );
}
