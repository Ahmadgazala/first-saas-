import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom does not implement object URLs.
if (!URL.createObjectURL) {
  URL.createObjectURL = () => "blob:mock";
  URL.revokeObjectURL = () => {};
}
