"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, apiUrl, pollJob } from "@/lib/api";
import { DEFAULT_SETTINGS, isKitStyle, validateSettings } from "@/lib/settings";
import type { Job, PuzzleSettings, SegmentationMethod, ShapeSource } from "@/lib/types";
import {
  DownloadButtons,
  GenerationProgress,
  ModelView,
  Notice,
  PieceInspector,
  QualityPanel,
  ResultStats,
  SheetView,
  SuitabilityPanel,
  usePuzzleModel,
} from "./ResultViews";
import { SettingsPanel } from "./SettingsPanel";
import { SkeletonEditor } from "./SkeletonEditor";
import { UploadDropzone, type SelectedImage } from "./UploadDropzone";

type Tab = "original" | "silhouette" | "structure" | "3d" | "sheet";

const METHODS: { value: SegmentationMethod; label: string }[] = [
  { value: "auto", label: "Automatic" },
  { value: "ai", label: "AI background removal" },
  { value: "alpha", label: "Transparency" },
  { value: "background", label: "Background color" },
  { value: "grabcut", label: "GrabCut" },
  { value: "threshold", label: "Brightness threshold" },
  { value: "edges", label: "Edge detection" },
];

const STEPS = ["Upload", "Silhouette", "Settings", "Generate", "Download"];

const SHAPE_OPTIONS: { value: ShapeSource; label: string; sub: string }[] = [
  { value: "auto", label: "Automatic", sub: "AI 3D, fallback to estimate" },
  { value: "ai_3d", label: "AI 3D", sub: "Reconstruct from photo" },
  { value: "silhouette", label: "Estimate", sub: "Fast, from outline" },
];

function ShapeSourcePicker({
  value,
  available,
  disabled,
  onChange,
}: {
  value: ShapeSource;
  available: boolean;
  disabled?: boolean;
  onChange: (v: ShapeSource) => void;
}) {
  return (
    <fieldset className="mb-4">
      <legend className="mb-2 flex w-full items-baseline justify-between text-sm font-semibold">
        <span>3D shape</span>
        <span className="text-xs font-normal text-muted">How the volume is obtained</span>
      </legend>
      <div role="radiogroup" aria-label="3D shape" className="grid grid-cols-3 gap-1.5">
        {SHAPE_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={value === o.value}
            disabled={disabled}
            onClick={() => onChange(o.value)}
            className={`rounded-lg border px-2 py-2 text-left text-sm transition disabled:opacity-50 ${
              value === o.value ? "border-ink bg-ink text-white" : "border-line bg-card hover:border-ink/40"
            }`}
          >
            <span className="block font-medium">{o.label}</span>
            <span className={`block text-xs ${value === o.value ? "text-white/70" : "text-muted"}`}>{o.sub}</span>
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted">
        {available
          ? "AI 3D turns the photo into a real 3D model and slices it — best for photos of real objects. Flat drawings and logos use the estimate automatically."
          : "AI 3D reconstruction is not installed on this server; the volume is estimated from the outline."}
      </p>
    </fieldset>
  );
}

function Stepper({ active }: { active: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-2 text-xs font-medium" aria-label="Progress">
      {STEPS.map((s, i) => {
        const state = i < active ? "done" : i === active ? "current" : "todo";
        return (
          <li key={s} className="flex items-center gap-2" aria-current={state === "current" ? "step" : undefined}>
            <span
              className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] ${
                state === "done" ? "bg-ok text-white" : state === "current" ? "bg-laser text-white" : "bg-black/10 text-muted"
              }`}
            >
              {state === "done" ? "✓" : i + 1}
            </span>
            <span className={state === "todo" ? "text-muted" : "text-ink"}>{s}</span>
            {i < STEPS.length - 1 && <span className="mx-1 hidden h-px w-6 bg-line sm:block" />}
          </li>
        );
      })}
    </ol>
  );
}

export function CreateWorkspace() {
  const [image, setImage] = useState<SelectedImage | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [settings, setSettings] = useState<PuzzleSettings>(DEFAULT_SETTINGS);
  const [uploading, setUploading] = useState(false);
  const [reextracting, setReextracting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("original");
  const [confirmed, setConfirmed] = useState(false);
  const [method, setMethod] = useState<SegmentationMethod>("auto");
  const abortRef = useRef<AbortController | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const errors = useMemo(() => validateSettings(settings), [settings]);
  const hasErrors = Object.keys(errors).length > 0;
  const silhouette = job?.silhouette ?? null;
  const result = job?.status === "completed" ? job.result : null;
  const { model, error: modelError } = usePuzzleModel(result?.model_url);
  const connected = model?.pieces.find((p) => p.id === selected)?.connected_to ?? [];
  const busy = uploading || reextracting || generating;

  useEffect(() => () => abortRef.current?.abort(), []);

  const handleImage = useCallback(async (img: SelectedImage | null) => {
    abortRef.current?.abort();
    setImage(img);
    setError(null);
    setConfirmed(false);
    setMethod("auto");
    // A new image has a new skeleton and suitability: drop edits made for the previous one.
    setSettings((s) => ({ ...s, skeleton: null, override_suitability: false }));
    if (!img) {
      setJob(null);
      setTab("original");
      return;
    }
    setTab("original");
    setUploading(true);
    try {
      const created = await api.createJob(img.file.name);
      const uploaded = await api.uploadImage(created.id, img.file);
      setJob(uploaded);
      setTab("silhouette");
    } catch (e) {
      setJob(null);
      setError(e instanceof ApiError ? e.message : "Upload failed. Please try another image.");
    } finally {
      setUploading(false);
    }
  }, []);

  const loadSample = async () => {
    setError(null);
    try {
      const res = await fetch("/samples/horse-silhouette.jpg");
      const blob = await res.blob();
      const file = new File([blob], "horse-silhouette.jpg", { type: "image/jpeg" });
      const url = URL.createObjectURL(file);
      await handleImage({ file, previewUrl: url, width: 1024, height: 1024 });
    } catch {
      setError("Could not load the sample image.");
    }
  };

  const reextract = async (m: SegmentationMethod) => {
    if (!job) return;
    setMethod(m);
    setReextracting(true);
    setError(null);
    setConfirmed(false);
    try {
      setJob(await api.reextract(job.id, m));
      setSettings((s) => ({ ...s, skeleton: null }));
      setTab("silhouette");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Silhouette detection failed.");
    } finally {
      setReextracting(false);
    }
  };

  const generate = async () => {
    if (!job || hasErrors) return;
    setGenerating(true);
    setError(null);
    setSelected(null);
    abortRef.current = new AbortController();
    try {
      const started = await api.generate(job.id, settings);
      setJob(started);
      const final = await pollJob(job.id, setJob, { signal: abortRef.current.signal });
      if (final.status === "completed") setTab("3d");
      else setError(final.error?.message ?? "Generation failed. Please try different settings.");
    } catch (e) {
      if (!(e instanceof ApiError && e.code === "ABORTED")) {
        setError(e instanceof ApiError ? e.message : "Generation failed. Please try again.");
      }
    } finally {
      setGenerating(false);
    }
  };

  const stepIndex = result ? 4 : generating ? 3 : silhouette ? 2 : image ? 1 : 0;
  const needsConfirm = Boolean(silhouette?.low_confidence);
  const suitability = job?.source_kind === "model" ? null : (job?.suitability ?? null);
  const blockedBySuitability = Boolean(suitability && !suitability.suitable && !settings.override_suitability);
  const canGenerate = Boolean(silhouette) && !hasErrors && !busy && (!needsConfirm || confirmed) && !blockedBySuitability;
  const noun = isKitStyle(settings.style) ? "Kit" : "Puzzle";

  const tabs: { id: Tab; label: string; enabled: boolean }[] = [
    { id: "original", label: "Original", enabled: Boolean(image) },
    { id: "silhouette", label: job?.source_kind === "model" ? "Outline" : "Silhouette", enabled: Boolean(silhouette) },
    { id: "structure", label: "Structure", enabled: Boolean(job?.structure_url) },
    { id: "3d", label: "3D preview", enabled: Boolean(result) },
    { id: "sheet", label: "Laser sheet", enabled: Boolean(result) },
  ];

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Create a kit</h1>
          <p className="mt-1 text-sm text-muted">Upload a photo, check the detected structure, tune the material, generate.</p>
        </div>
        <Stepper active={stepIndex} />
      </div>

      <div className="mb-4 space-y-3">
        <GenerationProgress job={job} />
        {error && <Notice tone="error" title="Something went wrong">{error}</Notice>}
      </div>

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        {/* LEFT: upload + settings */}
        <div className="space-y-6">
          <section className="rounded-2xl border border-line bg-card p-5 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">1. Photo or 3D model</h2>
              {!image && (
                <button type="button" onClick={loadSample} className="text-xs font-medium text-laser hover:underline">
                  Try a sample image
                </button>
              )}
            </div>
            <UploadDropzone value={image} onChange={handleImage} disabled={busy} busy={uploading} />
            {settings.style === "kit" && job?.source_kind !== "model" && (
              <div
                className="mt-3 rounded-xl border border-laser/40 bg-laser-soft px-3 py-3 text-xs text-ink-soft"
                data-testid="kit-stl-hint"
              >
                <p className="font-semibold text-ink">For the best fossil kit, add a 3D model (STL, OBJ, GLB or PLY)</p>
                <p className="mt-1">
                  A single photo has to be turned into 3D by guessing the hidden side. With a 3D model every rib is cut from
                  the real body, so the kit follows its true width and shape.
                </p>
                <label
                  htmlFor="model-file-input"
                  className="mt-2 inline-block cursor-pointer rounded-lg bg-ink px-3 py-1.5 font-medium text-white hover:bg-ink/90"
                >
                  Add STL / 3D model
                </label>
              </div>
            )}
            {silhouette && job?.source_kind !== "model" && (
              <label className="mt-3 block">
                <span className="mb-1 block text-xs font-medium text-ink-soft">Detection method</span>
                <select
                  value={method}
                  disabled={busy}
                  onChange={(e) => reextract(e.target.value as SegmentationMethod)}
                  className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-laser"
                >
                  {METHODS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </section>

          <section className="rounded-2xl border border-line bg-card p-5 shadow-sm">
            <h2 className="mb-4 font-semibold">2. Puzzle settings</h2>
            {job?.source_kind === "model" ? (
              <p className="mb-4 rounded-lg bg-wood-soft px-3 py-2 text-xs text-ink-soft">
                3D model uploaded: every piece will be an exact section of your model.
              </p>
            ) : (
              <ShapeSourcePicker
                value={settings.shape_source}
                available={job ? job.ai_3d_available : true}
                disabled={generating}
                onChange={(shape_source) => setSettings({ ...settings, shape_source })}
              />
            )}
            <SettingsPanel value={settings} onChange={setSettings} errors={errors} disabled={generating} />
          </section>
        </div>

        {/* RIGHT: previews */}
        <section className="flex min-w-0 flex-col gap-4">
          <div className="rounded-2xl border border-line bg-card p-3 shadow-sm">
            <div role="tablist" aria-label="Preview" className="mb-3 flex gap-1 overflow-x-auto">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  type="button"
                  aria-selected={tab === t.id}
                  disabled={!t.enabled}
                  onClick={() => setTab(t.id)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${
                    tab === t.id ? "bg-ink text-white" : "text-ink-soft hover:bg-black/5"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="h-[460px] sm:h-[540px]">
              {!image && (
                <div className="grid-paper flex h-full flex-col items-center justify-center rounded-2xl border border-dashed border-line text-center">
                  <p className="font-semibold">Your preview appears here</p>
                  <p className="mt-1 max-w-sm text-sm text-muted">
                    Original image, extracted silhouette, the generated 3D structure and the nested laser sheet.
                  </p>
                </div>
              )}
              {image && tab === "original" && (
                <div className="checker flex h-full items-center justify-center rounded-2xl border border-line p-4">
                  {job?.image?.url || image.previewUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={job?.image?.url ? apiUrl(job.image.url) : image.previewUrl} alt="Original upload" className="max-h-full max-w-full object-contain" />
                  ) : (
                    <p className="text-sm text-muted">Preparing the 3D model preview…</p>
                  )}
                </div>
              )}
              {tab === "silhouette" && silhouette && (
                <div className="relative flex h-full items-center justify-center rounded-2xl border border-line bg-black p-4">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    key={`${silhouette.url}-${silhouette.method}-${job?.updated_at}`}
                    src={`${apiUrl(silhouette.url)}?v=${encodeURIComponent(job?.updated_at ?? "")}`}
                    alt="Extracted silhouette (white = object)"
                    className="max-h-full max-w-full object-contain"
                  />
                  <span className="absolute left-3 top-3 rounded-lg bg-white/90 px-2 py-1 text-xs font-medium">
                    {silhouette.method} · confidence {Math.round((silhouette.confidence ?? 0) * 100)}%
                  </span>
                  {reextracting && <span className="absolute inset-0 flex items-center justify-center bg-black/50 text-sm text-white">Re-detecting…</span>}
                </div>
              )}
              {tab === "structure" && isKitStyle(settings.style) && suitability?.skeleton && job?.image?.url && (
                <SkeletonEditor
                  imageUrl={apiUrl(job.image.url)}
                  value={settings.skeleton ?? suitability.skeleton}
                  edited={Boolean(settings.skeleton)}
                  onChange={(skeleton) => setSettings({ ...settings, skeleton })}
                  onReset={() => setSettings({ ...settings, skeleton: null })}
                />
              )}
              {tab === "structure" && !(isKitStyle(settings.style) && suitability?.skeleton) && job?.structure_url && (
                <div className="relative flex h-full items-center justify-center rounded-2xl border border-line bg-paper p-4">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`${apiUrl(job.structure_url)}?v=${encodeURIComponent(job.updated_at)}`}
                    alt="Detected structure: spine with head, neck, torso and tail, and limb axes"
                    className="max-h-full max-w-full object-contain"
                  />
                  <span className="absolute left-3 top-3 rounded-lg bg-white/90 px-2 py-1 text-xs font-medium">
                    <span className="text-[#ef4444]">head</span> · <span className="text-[#f59e0b]">neck</span> ·{" "}
                    <span className="text-[#10b981]">torso</span> · <span className="text-[#8b5cf6]">tail</span> ·{" "}
                    <span className="text-[#0ea5e9]">limbs</span>
                  </span>
                </div>
              )}
              {tab === "3d" && result && (
                <ModelView model={model} error={modelError} className="h-full" selectedId={selected} onSelect={setSelected} />
              )}
              {tab === "sheet" && result && (
                <SheetView result={result} className="h-full" selectedId={selected} connected={connected} onSelect={setSelected} />
              )}
            </div>
          </div>

          {suitability && !result && (
            <SuitabilityPanel
              suitability={suitability}
              override={settings.override_suitability}
              onOverride={(v) => setSettings({ ...settings, override_suitability: v })}
            />
          )}

          {silhouette && (silhouette.low_confidence || silhouette.warnings.length > 0) && (
            <Notice tone="warn" title={silhouette.low_confidence ? "Please check the silhouette" : undefined}>
              <ul className="list-disc space-y-0.5 pl-4">
                {silhouette.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
              {needsConfirm && (
                <label className="mt-2 flex items-center gap-2 font-medium">
                  <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} className="accent-laser" />
                  The white shape matches my object — continue anyway
                </label>
              )}
            </Notice>
          )}

          {result && (
            <div className="space-y-4 rounded-2xl border border-line bg-card p-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">Your {noun.toLowerCase()} is ready</h2>
                <Link href={`/jobs/${job?.id}`} className="text-sm font-medium text-laser hover:underline">
                  Open job page →
                </Link>
              </div>
              {job && job.warnings.length > 0 && (
                <Notice tone="info">
                  <ul className="list-disc space-y-0.5 pl-4">
                    {job.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </Notice>
              )}
              <PieceInspector model={model} selectedId={selected} onSelect={setSelected} material={result.material} />
              <ResultStats result={result} />
              <QualityPanel result={result} />
              <DownloadButtons result={result} />
            </div>
          )}
        </section>
      </div>

      <div className="sticky bottom-0 z-20 mt-6 border-t border-line bg-paper/90 py-4 backdrop-blur">
        <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-between">
          <p className={`text-xs ${error ? "font-medium text-err" : "text-muted"}`} aria-live="polite">
            {error
              ? error
              : hasErrors
              ? "Fix the highlighted settings to continue."
              : !silhouette
                ? "Upload an image to start."
                : needsConfirm && !confirmed
                  ? "Confirm the silhouette above to continue."
                  : blockedBySuitability
                    ? "The image looks unsuitable - upload a clearer side view or choose “Generate anyway”."
                    : "Geometry is validated before any file is offered for download."}
          </p>
          <button
            type="button"
            onClick={generate}
            disabled={!canGenerate}
            className="w-full rounded-xl bg-laser px-8 py-3 text-base font-semibold text-white shadow-sm transition hover:bg-laser-dark disabled:cursor-not-allowed disabled:bg-black/20 sm:w-auto"
          >
            {generating ? "Generating…" : result ? `Regenerate ${noun}` : `Generate ${noun}`}
          </button>
        </div>
      </div>
    </div>
  );
}
