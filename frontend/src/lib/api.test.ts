import { describe, expect, it, vi } from "vitest";
import { makeJob } from "@/test/fakes";
import { ApiError, api, apiUrl, pollJob } from "./api";

function respond(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("api client", () => {
  it("builds absolute URLs", () => {
    expect(apiUrl("/api/jobs/1")).toBe("http://localhost:8000/api/jobs/1");
    expect(apiUrl("https://x.test/a")).toBe("https://x.test/a");
  });

  it("surfaces structured backend errors", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      respond(422, { success: false, error: { code: "PIECES_DO_NOT_FIT", message: "These pieces do not fit." } }),
    );
    await expect(api.getJob("j1")).rejects.toMatchObject({ code: "PIECES_DO_NOT_FIT", message: "These pieces do not fit.", status: 422 });
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.getJob("j1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("NETWORK_ERROR");
  });

  it("handles non-JSON error responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 }));
    await expect(api.getJob("j1")).rejects.toMatchObject({ code: "HTTP_ERROR", status: 502 });
  });

  it("uploads as multipart form data", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(200, makeJob()));
    await api.uploadImage("j1", new File(["x"], "lion.png", { type: "image/png" }));
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/jobs/j1/upload");
    expect((init as RequestInit).body).toBeInstanceOf(FormData);
  });

  it("polls until the job is terminal", async () => {
    const states = [makeJob({ status: "processing", progress: 30 }), makeJob({ status: "completed", progress: 100 })];
    vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(200, states.shift()));
    const seen: string[] = [];
    const final = await pollJob("j1", (j) => seen.push(j.status), { intervalMs: 1 });
    expect(final.status).toBe("completed");
    expect(seen).toEqual(["processing", "completed"]);
  });
});
