import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RESULT, makeJob } from "@/test/fakes";
import type { Job } from "@/lib/types";

vi.mock("./ResultViews", async (orig) => {
  const actual = await orig<typeof import("./ResultViews")>();
  return { ...actual, ModelView: () => <div data-testid="model-view" />, SheetView: () => <div data-testid="sheet-view" /> };
});
// A plain function (not vi.fn) so a rejecting implementation is not tracked as an unhandled rejection.
const backend = vi.hoisted(() => ({ getJob: async (id: string): Promise<Job> => Promise.reject(new Error(`unset: ${id}`)) }));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  Object.assign(actual.api, { getJob: (id: string) => backend.getJob(id) });
  return actual;
});

import { ApiError } from "@/lib/api";
import { JobDetails } from "./JobDetails";

describe("JobDetails", () => {
  it("shows a completed job with all specifications and downloads", async () => {
    backend.getJob = async () => makeJob({ status: "completed", result: RESULT });
    render(<JobDetails id="j1" />);
    expect(await screen.findByText("completed")).toBeInTheDocument();
    expect(screen.getByAltText("Original image")).toBeInTheDocument();
    expect(screen.getByAltText("Extracted silhouette")).toBeInTheDocument();
    expect(screen.getByTestId("model-view")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-view")).toBeInTheDocument();
    expect(screen.getByText("250 × 221 × 64 mm")).toBeInTheDocument();
    expect(screen.getByText("0.15 / 0.1 mm")).toBeInTheDocument();
    expect(screen.getByText("600 × 400 mm")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Download/ })).toHaveLength(4);
  });

  it("shows the failure reason", async () => {
    backend.getJob = async () =>
      makeJob({ status: "failed", error: { code: "INVALID_GEOMETRY", message: "Generated geometry is invalid." } });
    render(<JobDetails id="j1" />);
    expect(await screen.findByText("Generated geometry is invalid.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Download/ })).not.toBeInTheDocument();
  });

  it("polls while the job is processing", async () => {
    const states = [makeJob({ status: "processing", stage: "validating", progress: 45 }), makeJob({ status: "completed", result: RESULT })];
    backend.getJob = async () => states.length > 1 ? states.shift()! : states[0];
    render(<JobDetails id="j1" />);
    expect(await screen.findByText(/Validating geometry/)).toBeInTheDocument();
    expect(await screen.findByText("completed", {}, { timeout: 3000 })).toBeInTheDocument();
  });

  it("handles unknown jobs without crashing", async () => {
    backend.getJob = async () => {
      throw new ApiError("JOB_NOT_FOUND", "Job not found.", 404);
    };
    render(<JobDetails id="nope" />);
    expect(await screen.findByText("Job not found.")).toBeInTheDocument();
    expect(screen.getByText("Job unavailable")).toBeInTheDocument();
  });
});
