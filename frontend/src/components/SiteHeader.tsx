import Link from "next/link";

export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight ${className}`}>
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
        <rect x="1" y="1" width="26" height="26" rx="7" fill="#0c1222" />
        <path d="M8 20V9h5v4h7v7" fill="none" stroke="#d7b07c" strokeWidth="2.2" strokeLinejoin="round" />
        <path d="M13 9v4" stroke="#ea4f1d" strokeWidth="2.2" />
        <circle cx="20" cy="8" r="1.6" fill="#ea4f1d" />
      </svg>
      <span>
        PuzzleCut <span className="text-laser">AI</span>
      </span>
    </span>
  );
}

const NAV = [
  { href: "/create", label: "Create" },
  { href: "/pricing", label: "Pricing" },
  { href: "/about", label: "About" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-line/80 bg-paper/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link href="/" aria-label="PuzzleCut AI home">
          <Logo className="text-lg" />
        </Link>
        <nav className="flex items-center gap-1 sm:gap-2">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className="hidden rounded-lg px-3 py-2 text-sm font-medium text-ink-soft hover:bg-black/5 hover:text-ink sm:block"
            >
              {n.label}
            </Link>
          ))}
          <Link
            href="/create"
            className="rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-ink/90"
          >
            Create Puzzle
          </Link>
        </nav>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-10 text-sm text-muted sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <Logo />
        <p>Laser-ready slot-together puzzles from a single image. Geometry validated before every download.</p>
        <div className="flex gap-4">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="hover:text-ink">
              {n.label}
            </Link>
          ))}
        </div>
      </div>
    </footer>
  );
}
