"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="mx-auto max-w-xl px-4 py-24 text-center">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="mt-2 text-ink-soft">This page hit an unexpected error. Your files and jobs are safe.</p>
      <div className="mt-6 flex justify-center gap-3">
        <button onClick={reset} className="rounded-xl bg-ink px-5 py-2.5 text-sm font-semibold text-white">
          Try again
        </button>
        <Link href="/" className="rounded-xl border border-line px-5 py-2.5 text-sm font-semibold">
          Home
        </Link>
      </div>
    </div>
  );
}
