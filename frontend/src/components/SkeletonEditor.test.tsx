import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Skeleton } from "@/lib/types";
import { SkeletonEditor } from "./SkeletonEditor";

const DETECTED: Skeleton = {
  spine: [[0.1, 0.5], [0.4, 0.45], [0.7, 0.4], [0.9, 0.3]],
  head_start: [0.8, 0.35],
  limbs: [{ kind: "limb", joints: [[0.3, 0.55], [0.3, 0.7], [0.3, 0.85], [0.33, 0.95]] }],
};

const latest: { value: Skeleton | null } = { value: null };

function Harness() {
  const [edited, setEdited] = useState<Skeleton | null>(null);
  const update = (s: Skeleton | null) => {
    latest.value = s;
    setEdited(s);
  };
  return (
    <SkeletonEditor
      imageUrl="/photo.png"
      value={edited ?? DETECTED}
      edited={Boolean(edited)}
      onChange={update}
      onReset={() => update(null)}
    />
  );
}

function stubRect() {
  const svg = screen.getByRole("img", { name: "Editable skeleton" });
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 750, right: 1000, bottom: 750, x: 0, y: 0, toJSON: () => ({}) });
  return svg;
}

describe("SkeletonEditor", () => {
  it("shows the detected spine, head and limb joints as handles", () => {
    render(<Harness />);
    expect(screen.getAllByTestId(/^handle-spine-/)).toHaveLength(4);
    expect(screen.getAllByTestId(/^handle-limb-0-/)).toHaveLength(4);
    expect(screen.getByTestId("handle-head")).toBeInTheDocument();
    expect(screen.getByText("Drag the points to correct the skeleton")).toBeInTheDocument();
  });

  it("drags a point and reports the edited skeleton in image fractions", () => {
    render(<Harness />);
    const svg = stubRect();
    fireEvent.pointerDown(screen.getByTestId("handle-spine-3"), { pointerId: 1 });
    fireEvent.pointerMove(svg, { clientX: 950, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(svg, { pointerId: 1 });
    expect(latest.value?.spine[3]).toEqual([0.95, 0.4]);
    expect(latest.value?.spine[0]).toEqual([0.1, 0.5]);
    expect(screen.getByText(/the kit will follow your skeleton/)).toBeInTheDocument();
  });

  it("adds and removes limbs, toggles the head and resets", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add limb" }));
    expect(latest.value?.limbs).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove limb" }));
    expect(latest.value?.limbs).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "No head" }));
    expect(latest.value?.head_start).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add head" }));
    expect(latest.value?.head_start).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reset to detected" }));
    expect(latest.value).toBeNull();
  });
});
