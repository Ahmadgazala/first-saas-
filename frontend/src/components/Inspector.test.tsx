import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { RESULT } from "@/test/fakes";
import type { ModelPiece, PuzzleModelData } from "@/lib/types";
import { PieceInspector, QualityPanel, SheetView } from "./ResultViews";

function piece(id: string, role: ModelPiece["role"], connected: string[]): ModelPiece {
  return {
    id, label: id, kind: role === "cross" ? "cross" : "profile", role, region: "torso",
    outer: [[0, 0], [10, 0], [10, 10]], holes: [], engrave: [],
    transform: { origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], n: [0, 0, 1] },
    explode_offset: [0, 0, 0], insertion: null, width_mm: 120.4, height_mm: 80.2, area_mm2: 5400, slots: 3,
    connected_to: connected, sheet: { rotation_deg: 90, bbox_mm: [12, 30, 132, 110] },
  };
}

const MODEL: PuzzleModelData = {
  thickness_mm: 3,
  object_size_mm: { width: 250, height: 200, depth: 70 },
  sheet_mm: { width: 600, height: 400 },
  pieces: [
    piece("LONG-01", "spine", ["CROSS-01", "CROSS-02"]),
    piece("DETAIL-HEAD-01", "detail", ["CROSS-02"]),
    piece("CROSS-01", "cross", ["LONG-01"]),
    piece("CROSS-02", "cross", ["LONG-01", "DETAIL-HEAD-01"]),
  ],
  joints: [],
};

function Harness() {
  const [sel, setSel] = useState<string | null>(null);
  return <PieceInspector model={MODEL} selectedId={sel} onSelect={setSel} />;
}

describe("PieceInspector", () => {
  it("lists pieces by role and shows details of a selected piece", () => {
    render(<Harness />);
    expect(screen.getByText("Backbones (1)")).toBeInTheDocument();
    expect(screen.getByText("Detail plates (1)")).toBeInTheDocument();
    expect(screen.getByText("Cross ribs (2)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "CROSS-02" }));
    expect(screen.getByText("Cross rib · torso")).toBeInTheDocument();
    expect(screen.getByText("120 × 80 mm")).toBeInTheDocument();
    expect(screen.getByText(/rotated 90°/)).toBeInTheDocument();
    expect(screen.getByText("Connects to (2)")).toBeInTheDocument();
    // Navigate through a connection.
    fireEvent.click(screen.getByRole("button", { name: "DETAIL-HEAD-01" }));
    expect(screen.getByText("Detail plate · torso")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByText(/Click a piece/)).toBeInTheDocument();
  });
});

describe("QualityPanel", () => {
  it("shows measured manufacturing metrics", () => {
    render(<QualityPanel result={RESULT} />);
    const panel = screen.getByTestId("quality-panel");
    expect(panel).toHaveTextContent("42 / 21 (19 load-bearing)");
    expect(panel).toHaveTextContent("2.61 mm");
    expect(panel).toHaveTextContent("3.10 mm");
    expect(panel).toHaveTextContent("3.96 mm");
    expect(panel).toHaveTextContent("47.7%");
    expect(panel).toHaveTextContent("12 (1 backbone · 2 side profile · 9 cross rib)");
  });
});

describe("SheetView selection", () => {
  it("highlights the selected piece and its connections and selects by click", async () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="600mm" height="400mm" viewBox="0 0 600 400">
      <g id="CUT"><path id="LONG-01-cut" d="M0 0L10 0L10 10Z"/><path id="CROSS-01-cut" d="M20 0L30 0L30 10Z"/>
      <path id="CROSS-02-cut" d="M40 0L50 0L50 10Z"/></g></svg>`;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(svg, { status: 200 }));
    const onSelect = vi.fn();
    const { rerender } = render(<SheetView result={RESULT} onSelect={onSelect} />);
    await waitFor(() => expect(document.querySelector("#LONG-01-cut")).not.toBeNull());
    rerender(<SheetView result={RESULT} selectedId="LONG-01" connected={["CROSS-01"]} onSelect={onSelect} />);
    expect(document.querySelector("#LONG-01-cut")).toHaveClass("is-selected");
    expect(document.querySelector("#CROSS-01-cut")).toHaveClass("is-connected");
    expect(document.querySelector("#CROSS-02-cut")).toHaveClass("is-dimmed");
    await act(async () => fireEvent.click(document.querySelector("#CROSS-02-cut")!));
    expect(onSelect).toHaveBeenCalledWith("CROSS-02");
  });
});
