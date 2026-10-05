import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, validateSettings } from "@/lib/settings";
import type { PuzzleSettings } from "@/lib/types";
import { SettingsPanel } from "./SettingsPanel";

// Mutable holder for the latest settings reported through onChange.
const state: { latest: PuzzleSettings } = { latest: DEFAULT_SETTINGS };
function Harness() {
  const [s, setS] = useState(DEFAULT_SETTINGS);
  const onChange = (next: PuzzleSettings) => {
    state.latest = next;
    setS(next);
  };
  return <SettingsPanel value={s} onChange={onChange} errors={validateSettings(s)} />;
}

describe("SettingsPanel", () => {
  beforeEach(() => {
    state.latest = DEFAULT_SETTINGS;
  });

  it("updates thickness and the derived slot width", () => {
    render(<Harness />);
    expect(screen.getByText("Slot width 3.10 mm")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "6 mm" }));
    expect(state.latest.thickness_mm).toBe(6);
    expect(screen.getByText("Slot width 6.10 mm")).toBeInTheDocument();
  });

  it("supports a custom thickness", () => {
    render(<Harness />);
    const group = screen.getByRole("radiogroup", { name: "Material thickness" });
    fireEvent.click(group.querySelector("button:last-child")!);
    fireEvent.change(screen.getByLabelText("Custom thickness"), { target: { value: "3.5" } });
    expect(state.latest.thickness_mm).toBe(3.5);
  });

  it("edits kerf and clearance and shows validation errors", () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("Kerf (laser cut width)"), { target: { value: "0.2" } });
    fireEvent.change(screen.getByLabelText("Clearance (fit play)"), { target: { value: "0.05" } });
    expect(state.latest).toMatchObject({ kerf_mm: 0.2, clearance_mm: 0.05 });
    fireEvent.change(screen.getByLabelText("Kerf (laser cut width)"), { target: { value: "2" } });
    expect(screen.getByText(/Kerf must be between 0 and 1 mm/)).toBeInTheDocument();
  });

  it("selects material, pieces, complexity, sheet, orientation and size", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("radio", { name: "Acrylic" }));
    fireEvent.click(screen.getByRole("radio", { name: "20" }));
    fireEvent.click(screen.getByRole("radio", { name: /Advanced/ }));
    fireEvent.click(screen.getByRole("radio", { name: "1200 × 600 mm" }));
    fireEvent.click(screen.getByRole("radio", { name: "Horizontal" }));
    fireEvent.click(screen.getByRole("radio", { name: /Large/ }));
    expect(state.latest).toMatchObject({
      material: "acrylic",
      kerf_mm: 0.2,
      piece_count: 20,
      complexity: "advanced",
      sheet_width_mm: 1200,
      sheet_height_mm: 600,
      orientation: "horizontal",
      puzzle_size: "large",
    });
  });

  it("supports custom sheet and custom size", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("radiogroup", { name: "Sheet size" }).querySelector("button:last-child")!);
    fireEvent.change(screen.getByLabelText("Sheet width"), { target: { value: "450" } });
    fireEvent.click(screen.getByRole("radiogroup", { name: "Puzzle size" }).querySelector("button:last-child")!);
    fireEvent.change(screen.getByLabelText("Custom size"), { target: { value: "320" } });
    expect(state.latest).toMatchObject({ sheet_width_mm: 450, puzzle_size: "custom", custom_size_mm: 320 });
  });

  it("toggles labels and outputs", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("switch", { name: "Piece labels" }));
    fireEvent.click(screen.getByLabelText("DXF"));
    expect(state.latest.labels).toBe(false);
    expect(state.latest.outputs).not.toContain("dxf");
  });
  it("switches the construction style (solid slice is the default)", () => {
    render(<Harness />);
    expect(screen.getByRole("radio", { name: /Solid slice/ })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: /Skeleton kit/ }));
    expect(state.latest.style).toBe("kit");
    fireEvent.click(screen.getByRole("radio", { name: /Egg-crate open/ }));
    expect(state.latest.style).toBe("skeleton");
    expect(screen.queryByTestId("kit-options")).not.toBeInTheDocument();
    expect(screen.queryByTestId("depth-control")).not.toBeInTheDocument();
  });

  it("controls the object depth for the solid-slice style", () => {
    render(<Harness />);
    expect(screen.getByTestId("depth-control")).toBeInTheDocument();
    expect(screen.getByTestId("depth-value")).toHaveTextContent("×1.00");
    fireEvent.change(screen.getByLabelText("Object depth"), { target: { value: "1.5" } });
    expect(state.latest.depth_scale).toBe(1.5);
    expect(screen.getByTestId("depth-value")).toHaveTextContent("×1.50");
    expect(screen.getByText("Slices (maximum)")).toBeInTheDocument();
  });

  it("edits fossil kit options: frame, name and rib count", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("radio", { name: /Skeleton kit/ }));
    expect(screen.getByTestId("kit-options")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Kit name"), { target: { value: "T-Rex" } });
    expect(state.latest.kit_title).toBe("T-Rex");
    fireEvent.change(screen.getByLabelText("Rib count"), { target: { value: "8" } });
    expect(state.latest.rib_count).toBe(8);
    fireEvent.click(screen.getByRole("button", { name: "Auto" }));
    expect(state.latest.rib_count).toBe(0);
    fireEvent.click(screen.getByLabelText("Kit frame"));
    expect(state.latest.kit_frame).toBe(false);
    expect(screen.queryByLabelText("Kit name")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Rib count"), { target: { value: "99" } });
    expect(screen.getByText(/Rib count must be a whole number/)).toBeInTheDocument();
  });
});
