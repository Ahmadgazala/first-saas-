"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ACCEPTED_EXTENSIONS, MODEL_EXTENSIONS, checkFileBasics, checkResolution, formatBytes, isModelFile } from "@/lib/settings";

export interface SelectedImage {
  file: File;
  previewUrl: string;
  width: number;
  height: number;
  warning?: string;
  isModel?: boolean;
}

interface Props {
  value: SelectedImage | null;
  onChange: (image: SelectedImage | null) => void;
  disabled?: boolean;
  busy?: boolean;
}

function readDimensions(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => reject(new Error("decode"));
    img.src = url;
  });
}

export function UploadDropzone({ value, onChange, disabled, busy }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => () => {
    if (value?.previewUrl) URL.revokeObjectURL(value.previewUrl);
  }, [value?.previewUrl]);

  const accept = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setError(null);
      const basic = checkFileBasics(file);
      if (!basic.ok) {
        setError(basic.error ?? "Invalid file.");
        return;
      }
      if (isModelFile(file)) {
        onChange({ file, previewUrl: "", width: 0, height: 0, isModel: true });
        return;
      }
      setChecking(true);
      const url = URL.createObjectURL(file);
      try {
        const { width, height } = await readDimensions(url);
        const res = checkResolution(width, height);
        if (!res.ok) {
          URL.revokeObjectURL(url);
          setError(res.error ?? "Invalid image.");
          return;
        }
        onChange({ file, previewUrl: url, width, height, warning: res.warning });
      } catch {
        URL.revokeObjectURL(url);
        setError("This image could not be read. It may be corrupted — please try another file.");
      } finally {
        setChecking(false);
      }
    },
    [onChange],
  );

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!disabled) void accept(e.dataTransfer.files?.[0]);
  };

  if (value) {
    return (
      <div className="rounded-2xl border border-line bg-card p-3">
        <div className="flex items-center gap-4">
          <div className="checker relative flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line">
            {value.isModel ? (
              <span className="rounded-md bg-ink px-2 py-1 text-xs font-bold text-white">3D</span>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={value.previewUrl} alt="Selected image preview" className="h-full w-full object-contain" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold" title={value.file.name}>
              {value.file.name}
            </p>
            <p className="text-xs text-muted">
              {value.isModel ? "3D model" : `${value.width} × ${value.height} px`} · {formatBytes(value.file.size)}
            </p>
            {busy && (
              <p className="mt-1 text-xs font-medium text-laser">
                {value.isModel ? "Repairing and orienting the model…" : "Detecting the object…"}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setError(null);
            }}
            disabled={disabled}
            className="rounded-lg border border-line px-3 py-1.5 text-xs font-medium text-ink-soft hover:bg-black/5 disabled:opacity-50"
          >
            Remove
          </button>
        </div>
        {value.warning && <p className="mt-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">{value.warning}</p>}
      </div>
    );
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload an image"
        aria-disabled={disabled}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !disabled) inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`grid-paper flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${
          dragging ? "border-laser bg-laser-soft" : "border-line bg-card hover:border-ink/30"
        } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
      >
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-ink text-white">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <p className="text-sm font-semibold">{checking ? "Checking image…" : "Drop an image here or click to browse"}</p>
        <p className="mt-1 text-xs text-muted">Photo: PNG, JPG or WEBP up to 15 MB · 3D model: STL, OBJ, GLB or PLY up to 50 MB</p>
        <p className="mt-3 text-xs text-ink-soft">Photos: one object, side view. A 3D model gives the most accurate puzzle.</p>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={[...ACCEPTED_EXTENSIONS, ...MODEL_EXTENSIONS].join(",") + ",image/png,image/jpeg,image/webp"}
        className="sr-only"
        id="model-file-input"
        data-testid="file-input"
        onChange={(e) => {
          void accept(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {error && (
        <p role="alert" className="mt-2 rounded-lg bg-err-soft px-3 py-2 text-sm text-err">
          {error}
        </p>
      )}
    </div>
  );
}
