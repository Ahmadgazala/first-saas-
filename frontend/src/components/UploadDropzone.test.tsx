import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { makeFile, mockImageDecode } from "@/test/fakes";
import { UploadDropzone } from "./UploadDropzone";

describe("UploadDropzone", () => {
  it("accepts a valid image via the file picker", async () => {
    mockImageDecode(800, 600);
    const onChange = vi.fn();
    render(<UploadDropzone value={null} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("lion.png", "image/png")] } });
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(onChange.mock.calls[0][0]).toMatchObject({ width: 800, height: 600 });
  });

  it("accepts drag and drop", async () => {
    mockImageDecode(500, 500);
    const onChange = vi.fn();
    render(<UploadDropzone value={null} onChange={onChange} />);
    fireEvent.drop(screen.getByRole("button", { name: /upload an image/i }), {
      dataTransfer: { files: [makeFile("wolf.webp", "image/webp")] },
    });
    await waitFor(() => expect(onChange).toHaveBeenCalled());
  });

  it("rejects unsupported types with a useful message", async () => {
    const onChange = vi.fn();
    render(<UploadDropzone value={null} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("doc.pdf", "application/pdf")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/PNG, JPG or WEBP/);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("rejects files over 15 MB", async () => {
    render(<UploadDropzone value={null} onChange={vi.fn()} />);
    fireEvent.change(screen.getByTestId("file-input"), {
      target: { files: [makeFile("big.jpg", "image/jpeg", 16 * 1024 * 1024)] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(/15 MB/);
  });

  it("rejects images that are too small or cannot be decoded", async () => {
    mockImageDecode(20, 20);
    const { unmount } = render(<UploadDropzone value={null} onChange={vi.fn()} />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("tiny.png", "image/png")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/too small/);
    unmount();
    mockImageDecode(0, 0, true);
    render(<UploadDropzone value={null} onChange={vi.fn()} />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [makeFile("bad.png", "image/png")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be read/);
  });

  it("shows a preview and allows removal", () => {
    const onChange = vi.fn();
    const value = { file: makeFile("lion.png", "image/png", 2048), previewUrl: "blob:x", width: 640, height: 480 };
    render(<UploadDropzone value={value} onChange={onChange} />);
    expect(screen.getByAltText("Selected image preview")).toHaveAttribute("src", "blob:x");
    expect(screen.getByText(/640 × 480 px/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
