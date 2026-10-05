import { describe, expect, it } from "vitest";
import { sanitizeSheetSvg } from "./ResultViews";

describe("sanitizeSheetSvg", () => {
  it("keeps geometry and strips anything executable", () => {
    const svg = sanitizeSheetSvg(
      `<svg xmlns="http://www.w3.org/2000/svg" width="600mm" height="400mm" viewBox="0 0 600 400" onload="alert(1)">
        <script>alert(2)</script><g id="CUT"><path d="M0 0L10 0L10 10Z" onclick="x()"/></g>
        <foreignObject><div>hi</div></foreignObject><a href="javascript:alert(3)"><path d="M1 1"/></a></svg>`,
    );
    expect(svg).not.toBeNull();
    expect(svg!.querySelector("script")).toBeNull();
    expect(svg!.querySelector("foreignObject")).toBeNull();
    expect(svg!.querySelector("a")).toBeNull();
    expect(svg!.getAttribute("onload")).toBeNull();
    expect(svg!.querySelector("path")!.getAttribute("onclick")).toBeNull();
    expect(svg!.getAttribute("viewBox")).toBe("0 0 600 400");
    expect(svg!.getAttribute("width")).toBeNull();
  });

  it("rejects non-SVG content", () => {
    expect(sanitizeSheetSvg("<html><body>nope</body></html>")).toBeNull();
    expect(sanitizeSheetSvg("not xml at all <")).toBeNull();
  });
});
