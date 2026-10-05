import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RESULT, makeFile, makeJob, mockImageDecode } from "@/test/fakes";

vi.mock("./ResultViews", async (orig) => {
  const actual = await orig<typeof import("./ResultViews")>();
  return {
    ...actual,
    ModelView: ({ model }: { model: { pieces: unknown[] } | null }) => (
      <div data-testid="model-view">{model ? `${model.pieces.length} pieces` : "loading"}</div>
    ),
    usePuzzleModel: (url: string | null | undefined) => ({
      model: url ? { pieces: [{}, {}, {}], joints: [] } : null,
      error: null,
    }),
    PieceInspector: () => <div data-testid="piece-inspector" />,
    SheetView: () => <div data-testid="sheet-view" />,
  };
});

const apiMock = vi.hoisted(() => ({
  createJob: vi.fn(),
  uploadImage: vi.fn(),
  reextract: vi.fn(),
  generate: vi.fn(),
  getJob: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  Object.assign(actual.api, apiMock); // patch in place so pollJob() sees the mocks too
  return actual;
});

import { CreateWorkspace } from "./CreateWorkspace";

async function uploadFile() {
  mockImageDecode(512, 512);
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("lion.png", "image/png")] } });
  await screen.findByRole("tab", { name: "Silhouette", selected: true });
}

beforeEach(() => {
  Object.values(apiMock).forEach((m) => m.mockReset());
  apiMock.createJob.mockResolvedValue(makeJob({ status: "created", silhouette: null, image: null }));
  apiMock.uploadImage.mockResolvedValue(makeJob());
});

describe("CreateWorkspace", () => {
  it("disables generation until an image is uploaded", () => {
    render(<CreateWorkspace />);
    expect(screen.getByRole("button", { name: "Generate Kit" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "3D preview" })).toBeDisabled();
  });

  it("asks skeleton-kit users for a 3D model (STL) and hides the hint once one is uploaded", async () => {
    render(<CreateWorkspace />);
    expect(screen.queryByTestId("kit-stl-hint")).not.toBeInTheDocument(); // solid slice works from one photo
    fireEvent.click(screen.getByRole("radio", { name: /Skeleton kit/ }));
    const hint = screen.getByTestId("kit-stl-hint");
    expect(hint).toHaveTextContent(/STL/);
    expect(screen.getByText("Add STL / 3D model")).toHaveAttribute("for", "model-file-input");
    expect(screen.getByTestId("file-input")).toHaveAttribute("id", "model-file-input");
    apiMock.uploadImage.mockResolvedValue(makeJob({ source_kind: "model" }));
    mockImageDecode(512, 512);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("lion.stl", "model/stl")] } });
    await waitFor(() => expect(screen.queryByTestId("kit-stl-hint")).not.toBeInTheDocument());
  });

  it("uploads, shows the silhouette, generates and offers downloads", async () => {
    let resolveGet: (v: unknown) => void = () => {};
    apiMock.generate.mockResolvedValue(makeJob({ status: "queued", stage: "queued", progress: 0 }));
    apiMock.getJob
      .mockResolvedValueOnce(makeJob({ status: "processing", stage: "nesting", progress: 55 }))
      .mockImplementationOnce(() => new Promise((r) => (resolveGet = r)));
    render(<CreateWorkspace />);
    await uploadFile();
    expect(apiMock.uploadImage).toHaveBeenCalledWith("j1", expect.any(File));
    expect(screen.getByAltText(/Extracted silhouette/)).toHaveAttribute("src", expect.stringContaining("/api/jobs/j1/silhouette"));

    fireEvent.click(screen.getByRole("button", { name: "Generate Kit" }));
    expect(await screen.findByText(/Nesting pieces on the sheet/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "55");
    expect(screen.getByRole("button", { name: "Generating…" })).toBeDisabled();

    await waitFor(() => expect(apiMock.getJob).toHaveBeenCalledTimes(2), { timeout: 3000 });
    await act(async () => resolveGet(makeJob({ status: "completed", progress: 100, result: RESULT })));
    expect(await screen.findByText("Your kit is ready")).toBeInTheDocument();
    expect(screen.getByTestId("model-view")).toHaveTextContent("3 pieces");
    expect(screen.getByTestId("piece-inspector")).toBeInTheDocument();
    expect(screen.getByTestId("quality-panel")).toHaveTextContent("19 load-bearing");
    const links = ["Download SVG", "Download DXF", "Download PDF", "Download Assembly Guide"].map((n) =>
      screen.getByRole("link", { name: new RegExp(n) }),
    );
    expect(links[0]).toHaveAttribute("href", "http://localhost:8000/api/jobs/j1/download/svg");
    expect(links[3]).toHaveAttribute("href", "http://localhost:8000/api/jobs/j1/download/assembly-guide");
    expect(screen.getByText("12 (21 joints)")).toBeInTheDocument();
    expect(screen.getAllByText(/78.3%/).length).toBeGreaterThan(0);
    expect(apiMock.generate.mock.calls[0][1]).toMatchObject({ thickness_mm: 3, kerf_mm: 0.15, clearance_mm: 0.1, piece_count: 40, style: "slice", depth_scale: 1 });
  }, 10000);

  it("shows generation failures from the backend", async () => {
    apiMock.generate.mockResolvedValue(makeJob({ status: "queued" }));
    const message = "These pieces do not fit on the selected sheet. Increase the sheet size or reduce the puzzle size.";
    apiMock.getJob.mockResolvedValue(makeJob({ status: "failed", error: { code: "PIECES_DO_NOT_FIT", message } }));
    render(<CreateWorkspace />);
    await uploadFile();
    fireEvent.click(screen.getByRole("button", { name: "Generate Kit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("Your kit is ready")).not.toBeInTheDocument();
  });

  it("shows upload errors", async () => {
    const { ApiError } = await import("@/lib/api");
    apiMock.uploadImage.mockRejectedValue(new ApiError("OBJECT_NOT_DETECTED", "Unable to detect the object.", 422));
    mockImageDecode(512, 512);
    render(<CreateWorkspace />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("blank.png", "image/png")] } });
    expect((await screen.findAllByText("Unable to detect the object.")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Generate Kit" })).toBeDisabled();
  });

  it("requires confirmation for a low-confidence silhouette", async () => {
    apiMock.uploadImage.mockResolvedValue(
      makeJob({
        silhouette: { url: "/s", method: "grabcut", confidence: 0.4, low_confidence: true, warnings: ["Segmentation confidence is low."] },
      }),
    );
    render(<CreateWorkspace />);
    await uploadFile();
    expect(screen.getByText("Please check the silhouette")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Generate Kit" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/matches my object/));
    expect(button).toBeEnabled();
  });

  it("shows image suitability and the detected structure, and blocks unsuitable images until overridden", async () => {
    apiMock.uploadImage.mockResolvedValue(
      makeJob({
        structure_url: "/api/jobs/j1/structure",
        suitability: {
          suitable: false, overall_confidence: 0.38, silhouette_confidence: 0.9, structure_confidence: 0.4,
          recommended_view: "side", scores: {}, warnings: [], blocking: ["The image seems to contain more than one main subject."],
          guidance: "Please upload a clearer side-view image containing one main subject.",
          structure: { has_head: true, limb_count: 4, spine: { length_mm: 300, children: [{ kind: "torso", length_mm: 120 }] } },
          confidence_kind: "estimated",
        },
      }),
    );
    apiMock.generate.mockResolvedValue(makeJob({ status: "queued" }));
    apiMock.getJob.mockResolvedValue(makeJob({ status: "completed", result: RESULT }));
    render(<CreateWorkspace />);
    await uploadFile();
    const panel = screen.getByTestId("suitability-panel");
    expect(panel).toHaveTextContent("38% estimated");
    expect(panel).toHaveTextContent("more than one main subject");
    expect(panel).toHaveTextContent("Detected: head, torso, 4 limbs.");
    expect(screen.getByRole("tab", { name: "Structure" })).toBeEnabled();
    const button = screen.getByRole("button", { name: "Generate Kit" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Generate anyway/));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(apiMock.generate).toHaveBeenCalled());
    expect(apiMock.generate.mock.calls[0][1]).toMatchObject({ style: "slice", override_suitability: true });
  });

  it("re-runs detection with another method", async () => {
    apiMock.reextract.mockResolvedValue(makeJob({ silhouette: { url: "/s", method: "threshold", confidence: 0.8, low_confidence: false, warnings: [] } }));
    render(<CreateWorkspace />);
    await uploadFile();
    fireEvent.change(screen.getByLabelText("Detection method"), { target: { value: "threshold" } });
    await waitFor(() => expect(apiMock.reextract).toHaveBeenCalledWith("j1", "threshold"));
    expect(await screen.findByText(/threshold · confidence 80%/)).toBeInTheDocument();
  });

  it("blocks generation when settings are invalid", async () => {
    render(<CreateWorkspace />);
    await uploadFile();
    fireEvent.change(screen.getByLabelText("Kerf (laser cut width)"), { target: { value: "5" } });
    expect(screen.getByRole("button", { name: "Generate Kit" })).toBeDisabled();
    expect(screen.getByText("Fix the highlighted settings to continue.")).toBeInTheDocument();
  });
});
