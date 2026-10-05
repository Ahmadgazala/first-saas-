import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-4 py-24 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-ink-soft">The page you are looking for does not exist.</p>
      <Link href="/create" className="mt-6 inline-block rounded-xl bg-laser px-5 py-2.5 text-sm font-semibold text-white">
        Create a puzzle
      </Link>
    </div>
  );
}
