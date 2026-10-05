"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, api, apiUrl, isTerminal } from "@/lib/api";
import type { Job } from "@/lib/types";
import {
  DownloadButtons,
  GenerationProgress,
  ModelView,
  Notice,
  PieceInspector,
  QualityPanel,
  ResultStats,
  SheetView,
  Stat,
  usePuzzleModel,
} from "./ResultViews";

const STATUS_STYLE: Record<string, string> = {
  completed: "bg-ok/10 text-ok",
  failed: "bg-err-soft text-err",
  processing: "bg-laser-soft text-laser-dark",
  queued: "bg-laser-soft text-laser-dark",
  uploaded: "bg-black/5 text-ink-soft",
  created: "bg-black/5 text-ink-soft",
};

function Panel({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-line bg-card p-4 shadow-sm ${className}`}>
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function JobDetails({ id }: { id: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const { model, error: modelError } = usePuzzleModel(job?.result?.model_url);
  const connected = model?.pieces.find((p) => p.id === selected)?.connected_to ?? [];

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const j = await api.getJob(id);
        if (!alive) return;
        setJob(j);
        setError(null);
        if (!isTerminal(j) && ["queued", "processing"].includes(j.status)) timer = setTimeout(load, 1000);
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.message : "This job could not be loaded.");
      }
    };
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16">
        <Notice tone="error" title="Job unavailable">
          {error}
        </Notice>
        <Link href="/create" className="mt-4 inline-block text-sm font-medium text-laser hover:underline">
          ← Create a new puzzle
        </Link>
      </div>
    );
  }
  if (!job) {
    return <div className="mx-auto h-96 max-w-7xl animate-pulse px-4 py-10 sm:px-6" aria-label="Loading job" />;
  }

  const r = job.result;
  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="font-mono text-xs text-muted">Job {job.id}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{job.image?.filename ?? "Puzzle job"}</h1>
        </div>
        <span className={`inline-flex w-fit items-center rounded-full px-3 py-1 text-sm font-semibold capitalize ${STATUS_STYLE[job.status]}`}>
          {job.status}
        </span>
      </div>

      <GenerationProgress job={job} />
      {job.status === "failed" && job.error && (
        <Notice tone="error" title="Generation failed">
          {job.error.message}
        </Notice>
      )}
      {job.status === "uploaded" && (
        <Notice tone="info">
          This job has a silhouette but no generated puzzle yet. <Link href="/create" className="font-medium text-laser">Create a puzzle</Link> to generate one.
        </Notice>
      )}
      {job.warnings.length > 0 && (
        <Notice tone="warn">
          <ul className="list-disc space-y-0.5 pl-4">
            {job.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Original image">
          <div className="checker flex h-64 items-center justify-center rounded-xl border border-line p-2">
            {job.image?.url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={apiUrl(job.image.url)} alt="Original image" className="max-h-full max-w-full object-contain" />
            ) : (
              <span className="text-sm text-muted">No image</span>
            )}
          </div>
        </Panel>
        <Panel title="Extracted silhouette">
          <div className="flex h-64 items-center justify-center rounded-xl bg-black p-2">
            {job.silhouette ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={apiUrl(job.silhouette.url)} alt="Extracted silhouette" className="max-h-full max-w-full object-contain" />
            ) : (
              <span className="text-sm text-white/60">No silhouette</span>
            )}
          </div>
          {job.silhouette && (
            <p className="mt-2 text-xs text-muted">
              Method: {job.silhouette.method} · confidence {Math.round((job.silhouette.confidence ?? 0) * 100)}%
            </p>
          )}
        </Panel>
      </div>

      {r && (
        <>
          <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
            <Panel title="3D preview">
              <ModelView model={model} error={modelError} className="h-[460px]" selectedId={selected} onSelect={setSelected} />
            </Panel>
            <Panel title="Laser sheet">
              <SheetView result={r} className="h-[460px]" selectedId={selected} connected={connected} onSelect={setSelected} />
            </Panel>
          </div>
          <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
            <QualityPanel result={r} />
            <PieceInspector model={model} selectedId={selected} onSelect={setSelected} material={job.result?.material} />
          </div>
          <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
            <Panel title="Specifications">
              <ResultStats result={r} />
            </Panel>
            <Panel title="Downloads">
              <DownloadButtons result={r} />
              <div className="mt-4 rounded-xl border border-line bg-paper p-3">
                <p className="mb-2 text-xs font-medium text-ink-soft">Rendered preview</p>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={apiUrl(r.preview_url)} alt="Rendered assembled puzzle" className="mx-auto max-h-48 object-contain" />
              </div>
            </Panel>
          </div>
        </>
      )}
      {!r && job.settings && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Material thickness" value={`${job.settings.thickness_mm} mm`} />
          <Stat label="Kerf" value={`${job.settings.kerf_mm} mm`} />
          <Stat label="Clearance" value={`${job.settings.clearance_mm} mm`} />
          <Stat label="Pieces" value={job.settings.piece_count} />
        </dl>
      )}
    </div>
  );
}
