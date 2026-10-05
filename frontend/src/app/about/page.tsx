import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

export const metadata: Metadata = { title: "About" };

export default function AboutPage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-16 sm:px-6">
      <h1 className="text-4xl font-semibold tracking-tight">How PuzzleCut works</h1>
      <p className="mt-4 text-lg text-ink-soft">
        PuzzleCut AI converts a single image into flat pieces that slot together into a recognizable 3D object — the same
        construction as classic laser-cut animal kits.
      </p>

      <div className="mt-10 overflow-hidden rounded-2xl border border-line bg-gradient-to-b from-white to-wood-soft">
        <Image src="/showcase/horse-exploded.png" alt="Exploded view of generated pieces" width={1200} height={900} className="h-auto w-full" />
      </div>

      <div className="prose-like mt-12 space-y-10 text-ink-soft">
        <section>
          <h2 className="text-xl font-semibold text-ink">1. Silhouette extraction</h2>
          <p className="mt-2">
            Several independent methods (transparency, a fitted background colour model, GrabCut, brightness thresholding
            and edge detection) each propose a foreground. Every candidate is cleaned up and scored on colour separation,
            edge alignment and consistency with the background; agreement between methods raises confidence. You always
            review the silhouette, and low-confidence results must be confirmed before generation.
          </p>
        </section>
        <section>
          <h2 className="text-xl font-semibold text-ink">2. Stylized depth</h2>
          <p className="mt-2">
            A single photo does not contain real 3D information, and we don&apos;t pretend it does. Depth is estimated
            deterministically by “inflating” the silhouette: wide regions such as a body become thick, thin regions such as
            legs and tails taper. The depth model is a replaceable module, so AI depth estimation can be added later.
          </p>
        </section>
        <section>
          <h2 className="text-xl font-semibold text-ink">3. Ribs and slots</h2>
          <p className="mt-2">
            The central profile rib is the silhouette itself. Side profiles follow the body where it is thick enough, and
            cross ribs are cut through the body at evenly spaced positions. Every place two ribs cross becomes a half-lap
            joint: each piece is slotted to the midpoint of their overlap, with a slot width of{" "}
            <strong className="text-ink">material thickness + clearance</strong>.
          </p>
        </section>
        <section>
          <h2 className="text-xl font-semibold text-ink">4. Kerf compensation</h2>
          <p className="mt-2">
            A laser removes a thin strip of material (the kerf) centred on its path. PuzzleCut offsets every cut contour
            outward by half the kerf, so the finished parts and slots come out exactly at their design size. In the file a
            3.10 mm slot is therefore drawn 2.95 mm wide for a 0.15 mm kerf. Engraved labels are never offset.
          </p>
        </section>
        <section>
          <h2 className="text-xl font-semibold text-ink">5. Validation and nesting</h2>
          <p className="mt-2">
            Before any file is written, every polygon, slot and label is validated, the 3D assembly is checked so that no
            two pieces interpenetrate, and every piece is nested onto your sheet with spacing and verified for overlaps.
            If something cannot be repaired safely, you get a clear error instead of a broken file.
          </p>
        </section>
        <section className="rounded-2xl border border-line bg-card p-6">
          <h2 className="text-xl font-semibold text-ink">Current limitations</h2>
          <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm">
            <li>The 3D shape is a stylized interpretation of the outline, not a reconstruction of the real object.</li>
            <li>Side-view silhouettes of a single object work best; busy photos may need a different detection method.</li>
            <li>Features thinner than the material can carry (thin legs, whiskers) are removed so pieces don&apos;t break.</li>
            <li>All pieces are nested on one sheet; larger puzzles need a larger sheet.</li>
            <li>Always test-cut a single joint to tune kerf and clearance for your machine and material.</li>
          </ul>
        </section>
      </div>
      <Link href="/create" className="mt-10 inline-block rounded-xl bg-laser px-6 py-3 font-semibold text-white hover:bg-laser-dark">
        Create Puzzle
      </Link>
    </div>
  );
}
