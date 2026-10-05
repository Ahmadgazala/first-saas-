import { describe, expect, it } from "vitest";
import { makeFile } from "@/test/fakes";
import {
  DEFAULT_SETTINGS,
  checkFileBasics,
  checkResolution,
  slotWidth,
  targetSize,
  toolpathSlotWidth,
  validateSettings,
} from "./settings";

describe("slot math", () => {
  it.each([
    [3, 0.1, 3.1],
    [4, 0.1, 4.1],
    [6, 0.1, 6.1],
    [9, 0.25, 9.25],
  ])("thickness %s + clearance %s = %s", (t, c, w) => {
    expect(slotWidth({ thickness_mm: t, clearance_mm: c })).toBe(w);
  });

  it("subtracts the kerf for the tool path", () => {
    expect(toolpathSlotWidth({ thickness_mm: 3, clearance_mm: 0.1, kerf_mm: 0.15 })).toBe(2.95);
  });
});

describe("validateSettings", () => {
  it("accepts the defaults", () => {
    expect(validateSettings(DEFAULT_SETTINGS)).toEqual({});
  });

  it("rejects out-of-range values", () => {
    const e = validateSettings({ ...DEFAULT_SETTINGS, thickness_mm: 0.1, piece_count: 2, sheet_width_mm: 10, outputs: [] });
    expect(Object.keys(e).sort()).toEqual(["outputs", "piece_count", "sheet_width_mm", "thickness_mm"]);
  });

  it("requires kerf below the slot width", () => {
    expect(validateSettings({ ...DEFAULT_SETTINGS, kerf_mm: 1, thickness_mm: 0.8, clearance_mm: 0 }).kerf_mm).toMatch(/slot width/);
  });

  it("validates custom size and custom material", () => {
    const s = { ...DEFAULT_SETTINGS, puzzle_size: "custom" as const, custom_size_mm: null, material: "custom" as const };
    const e = validateSettings(s);
    expect(e.custom_size_mm).toBeDefined();
    expect(e.custom_material_name).toBeDefined();
    expect(targetSize({ ...DEFAULT_SETTINGS, puzzle_size: "large" })).toBe(400);
  });

  it("flags a size too small for the thickness", () => {
    const e = validateSettings({ ...DEFAULT_SETTINGS, puzzle_size: "custom", custom_size_mm: 60, thickness_mm: 9 });
    expect(e.custom_size_mm).toMatch(/too small/);
  });

  it("rejects fractional piece counts", () => {
    expect(validateSettings({ ...DEFAULT_SETTINGS, piece_count: 7.5 }).piece_count).toBeDefined();
  });
});

describe("file checks", () => {
  it("accepts png/jpg/webp", () => {
    expect(checkFileBasics(makeFile("a.png", "image/png")).ok).toBe(true);
    expect(checkFileBasics(makeFile("a.jpeg", "image/jpeg")).ok).toBe(true);
    expect(checkFileBasics(makeFile("a.webp", "image/webp")).ok).toBe(true);
  });

  it("rejects other types, empty and oversized files", () => {
    expect(checkFileBasics(makeFile("a.gif", "image/gif")).error).toMatch(/Unsupported/);
    expect(checkFileBasics(makeFile("a.png", "image/png", 0)).error).toMatch(/empty/);
    expect(checkFileBasics(makeFile("a.png", "image/png", 16 * 1024 * 1024)).error).toMatch(/15 MB/);
  });

  it("checks resolution", () => {
    expect(checkResolution(32, 500).ok).toBe(false);
    expect(checkResolution(9000, 500).ok).toBe(false);
    expect(checkResolution(5000, 3000)).toMatchObject({ ok: true, warning: expect.stringMatching(/downscaled/) });
    expect(checkResolution(1024, 768)).toEqual({ ok: true });
  });
});
