import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Pricing" };

const PLANS = [
  {
    name: "Beta",
    price: "Free",
    note: "while PuzzleCut is in beta",
    features: ["Unlimited puzzles", "SVG, DXF, PDF and assembly guide", "Up to 60 pieces", "Custom materials and sheets"],
    cta: "Create Puzzle",
    highlight: true,
  },
  {
    name: "Maker",
    price: "Planned",
    note: "for hobbyists after beta",
    features: ["Saved projects", "Multi-sheet layouts", "Priority processing"],
    cta: null,
    highlight: false,
  },
  {
    name: "Studio",
    price: "Planned",
    note: "for shops selling kits",
    features: ["Commercial license", "Batch generation", "Team accounts and API access"],
    cta: null,
    highlight: false,
  },
];

export default function PricingPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <div className="mx-auto max-w-2xl text-center">
        <h1 className="text-4xl font-semibold tracking-tight">Simple pricing</h1>
        <p className="mt-3 text-ink-soft">
          Everything is free during the beta. Paid plans are not available yet — the tiers below show what is planned.
        </p>
      </div>
      <div className="mt-12 grid gap-4 md:grid-cols-3">
        {PLANS.map((p) => (
          <div
            key={p.name}
            className={`flex flex-col rounded-2xl border p-6 ${p.highlight ? "border-ink bg-ink text-white shadow-xl" : "border-line bg-card"}`}
          >
            <h2 className="font-semibold">{p.name}</h2>
            <p className="mt-4 text-4xl font-semibold tracking-tight">{p.price}</p>
            <p className={`text-sm ${p.highlight ? "text-white/60" : "text-muted"}`}>{p.note}</p>
            <ul className="mt-6 flex-1 space-y-2 text-sm">
              {p.features.map((f) => (
                <li key={f} className="flex gap-2">
                  <span className="text-laser">✓</span> {f}
                </li>
              ))}
            </ul>
            {p.cta ? (
              <Link href="/create" className="mt-6 rounded-xl bg-laser px-4 py-2.5 text-center font-semibold text-white hover:bg-laser-dark">
                {p.cta}
              </Link>
            ) : (
              <span className="mt-6 rounded-xl border border-line px-4 py-2.5 text-center text-sm text-muted">Coming later</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
