"""Compare two harness runs (medians per mode and case kind, plus failures).

    .venv/bin/python -m fidelity.compare fidelity/reports/phase2 fidelity/reports/phase3_mesh_driven

Reads <run>/results.csv (written by fidelity.report) and prints a Markdown table "A -> B".
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

METRICS = ("side_iou", "front_iou_3d", "top_iou_3d", "volume_iou", "coverage_6mm", "material_outside_ref",
           "chamfer_mm", "kit_vs_body25d_coverage_6mm")


def load(run: Path) -> list[dict]:
    with open(run / "results.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def med(rows: list[dict], key: str):
    vals = []
    for r in rows:
        try:
            vals.append(float(r.get(key) or "nan"))
        except ValueError:
            continue
    vals = [v for v in vals if v == v]
    return (statistics.median(vals), len(vals)) if vals else (None, 0)


def fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    args = ap.parse_args()
    A, B = load(Path(args.a)), load(Path(args.b))
    groups = sorted({(r["mode"], r["kind"]) for r in A + B})
    print("| mode | kind | ok A -> B | " + " | ".join(METRICS) + " |")
    print("|---" * (len(METRICS) + 3) + "|")
    for mode, kind in groups:
        a = [r for r in A if r["mode"] == mode and r["kind"] == kind]
        b = [r for r in B if r["mode"] == mode and r["kind"] == kind]
        ok_a = sum(r["status"] == "ok" for r in a)
        ok_b = sum(r["status"] == "ok" for r in b)
        cells = []
        for m in METRICS:
            va, _ = med([r for r in a if r["status"] == "ok"], m)
            vb, _ = med([r for r in b if r["status"] == "ok"], m)
            cells.append(f"{fmt(va)} -> {fmt(vb)}" if (va is not None or vb is not None) else "-")
        print(f"| {mode} | {kind} | {ok_a}/{len(a)} -> {ok_b}/{len(b)} | " + " | ".join(cells) + " |")
    fails = [(r["case"], r["mode"], r["status"], r.get("error", "")[:90]) for r in B if r["status"] != "ok"]
    if fails:
        print("\nNot ok in B:")
        for f in fails:
            print("-", " | ".join(f))


if __name__ == "__main__":
    main()
