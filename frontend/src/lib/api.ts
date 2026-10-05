import type { ApiErrorBody, Job, PuzzleModelData, PuzzleSettings, SegmentationMethod } from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Absolute URL for a backend-relative path such as /api/jobs/{id}/preview. */
export function apiUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), { ...init, cache: "no-store" });
  } catch {
    throw new ApiError(
      "NETWORK_ERROR",
      "Cannot reach the PuzzleCut server. Check that the backend is running and try again.",
      0,
    );
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const err = (body as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(
      err?.code ?? "HTTP_ERROR",
      err?.message ?? `The server returned an unexpected error (${response.status}). Please try again.`,
      response.status,
    );
  }
  return body as T;
}

const json = (data: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export const api = {
  createJob: (name?: string) => request<Job>("/api/jobs", json({ name: name ?? null })),
  getJob: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  uploadImage: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<Job>(`/api/jobs/${encodeURIComponent(id)}/upload`, { method: "POST", body: form });
  },
  reextract: (id: string, method: SegmentationMethod) =>
    request<Job>(`/api/jobs/${encodeURIComponent(id)}/silhouette`, json({ method })),
  analyze: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}/analyze`, { method: "POST" }),
  generate: (id: string, settings: PuzzleSettings) =>
    request<Job>(`/api/jobs/${encodeURIComponent(id)}/generate`, json(settings)),
  getModel: (url: string) => request<PuzzleModelData>(url),
};

export const DOWNLOAD_LABELS: Record<string, string> = {
  svg: "Download SVG",
  dxf: "Download DXF",
  pdf: "Download PDF",
  assembly_guide: "Download Assembly Guide",
};

export function isTerminal(job: Job | null): boolean {
  return job?.status === "completed" || job?.status === "failed";
}

/** Poll a job until it completes or fails. Resolves with the final job. */
export async function pollJob(
  id: string,
  onUpdate: (job: Job) => void,
  { intervalMs = 800, timeoutMs = 600_000, signal }: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Job> {
  const started = Date.now();
  for (;;) {
    if (signal?.aborted) throw new ApiError("ABORTED", "Cancelled.", 0);
    const job = await api.getJob(id);
    onUpdate(job);
    if (isTerminal(job)) return job;
    if (Date.now() - started > timeoutMs) {
      throw new ApiError("TIMEOUT", "Generation is taking longer than expected. Check the job page later.", 0);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
