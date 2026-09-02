import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { OrionField } from "@/components/OrionField";
import { RIBBONS, RIBBONS_FRONT } from "@/lib/field-geometry";
import { ORION_VISUAL_STATES } from "@/state/orion-visual-state";

afterEach(cleanup);

/**
 * The chamber, and one committed guarantee about how it moves.
 *
 * THE GUARANTEE IS NOT DECORATIVE. The ribbon paths carry
 * `vector-effect: non-scaling-stroke`, because the viewBox is stretched
 * non-uniformly to the width of the screen and a user-unit stroke would be
 * sub-pixel on a phone and heavy on a desktop. A non-scaling stroke is
 * derived against the element's *current* accumulated transform, so animating
 * anything inside that SVG makes Blink re-derive every outline and
 * re-rasterise the whole band on every frame. Measured in Chrome at
 * 1920x1080 that single interaction was the entire frame budget: 20.8ms per
 * frame with the waves animated inside the SVG, 6.9ms with the identical
 * waves animated from outside it.
 *
 * So the rule this file exists to hold is exactly one sentence: nothing
 * inside a ribbon's `<svg>` may move. The lane and the sway are boxes around
 * it, the SVG is rasterised once, and the motion is a composited transform of
 * a finished texture. A future edit that puts a `<g>` back around these paths
 * would look identical in a screenshot and cost three times the frame.
 */

const lanesOf = (testId: string): HTMLElement[] =>
  [...screen.getByTestId(testId).children] as HTMLElement[];

describe("the chamber", () => {
  it("renders in every visual state and tags the one it is in", () => {
    for (const state of ORION_VISUAL_STATES) {
      const { container, unmount } = render(<OrionField state={state} />);
      const chamber = container.querySelector("[data-state]");
      expect(chamber?.getAttribute("data-state")).toBe(state);
      unmount();
    }
  });

  it("takes part in no layout and cannot be reached", () => {
    // The room is absolutely positioned behind the composition and hidden
    // from assistive technology; it must not be focusable or announced.
    const { container } = render(<OrionField state="idle" />);
    expect(container.querySelectorAll("[aria-hidden='true']").length).toBeGreaterThan(0);
    expect(container.querySelectorAll("button, a, input, [tabindex]")).toHaveLength(0);
    expect(container.textContent).toBe("");
  });

  it("draws both wave bands, back and front", () => {
    render(<OrionField state="idle" />);
    expect(lanesOf("orion-ribbons")).toHaveLength(RIBBONS.length);
    expect(lanesOf("orion-ribbons-front")).toHaveLength(RIBBONS_FRONT.length);
  });
});

describe("the waves animate a box, never the stroke", () => {
  const bands = ["orion-ribbons", "orion-ribbons-front"] as const;

  it("keeps every band container an ordinary element rather than an SVG", () => {
    render(<OrionField state="idle" />);
    for (const band of bands) {
      const container = screen.getByTestId(band);
      expect(container).toBeInstanceOf(HTMLElement);
      expect(container).not.toBeInstanceOf(SVGElement);
    }
  });

  it("puts the drift and the sway on elements outside the SVG", () => {
    render(<OrionField state="idle" />);
    for (const band of bands) {
      for (const lane of lanesOf(band)) {
        // The lane carries the drift...
        expect(lane).toBeInstanceOf(HTMLElement);
        expect(lane).not.toBeInstanceOf(SVGElement);
        // ...and the sway sits inside it, still outside the SVG.
        const sway = lane.firstElementChild;
        expect(sway).toBeInstanceOf(HTMLElement);
        expect(sway).not.toBeInstanceOf(SVGElement);
        expect(sway?.firstElementChild).toBeInstanceOf(SVGSVGElement);
      }
    }
  });

  it("leaves nothing inside a ribbon SVG that could be animated", () => {
    // A `<g>` here is the regression: it is the only thing that would put a
    // transform back inside the non-scaling stroke's coordinate system.
    render(<OrionField state="idle" />);
    for (const band of bands) {
      const container = screen.getByTestId(band);
      expect(container.querySelectorAll("g")).toHaveLength(0);
      for (const svg of container.querySelectorAll("svg")) {
        expect([...svg.children].every((child) => child.tagName === "path")).toBe(true);
        expect(svg.getAttribute("transform")).toBeNull();
      }
    }
  });

  it("gives every lane a two-period viewBox, so the -50% drift has no seam", () => {
    render(<OrionField state="idle" />);
    for (const band of bands) {
      for (const svg of screen.getByTestId(band).querySelectorAll("svg")) {
        // 2000 user units is two periods of a 1000-unit wave: the frame after
        // the loop restarts is identical to the frame before it.
        expect(svg.getAttribute("viewBox")).toBe("0 0 2000 400");
        expect(svg.getAttribute("preserveAspectRatio")).toBe("none");
      }
    }
  });

  it("draws each wave as two strokes on one path and no filter", () => {
    render(<OrionField state="idle" />);
    for (const lane of lanesOf("orion-ribbons")) {
      const paths = lane.querySelectorAll("path");
      expect(paths).toHaveLength(2);
      // Identical geometry: the bloom is a wider, fainter companion stroke,
      // not a drop-shadow, because a filter here re-rasterises every frame.
      expect(paths[0]?.getAttribute("d")).toBe(paths[1]?.getAttribute("d"));
      expect(lane.querySelector("filter")).toBeNull();
    }
  });

  it("keeps the depth tier on the lane, where the stylesheet reads it", () => {
    render(<OrionField state="idle" />);
    const depths = lanesOf("orion-ribbons").map((lane) => lane.getAttribute("data-depth"));
    expect(depths).toEqual(RIBBONS.map((ribbon) => String(ribbon.depth)));
  });
});

describe("the room is generated, not hand-placed", () => {
  it("spans two full periods so the loop cannot show a seam", () => {
    for (const ribbon of [...RIBBONS, ...RIBBONS_FRONT]) {
      expect(ribbon.d.startsWith("M0 ")).toBe(true);
      // The last sample is at x = 2000: exactly twice the 1000-unit period
      // the lane travels.
      expect(ribbon.d.split("L").at(-1)?.startsWith("2000 ")).toBe(true);
    }
  });

  it("shares no drift period between ribbons, so the field never repeats", () => {
    const drifts = RIBBONS.map((ribbon) => ribbon.drift);
    expect(new Set(drifts).size).toBe(drifts.length);
    const sways = RIBBONS.map((ribbon) => ribbon.sway);
    expect(new Set(sways).size).toBe(sways.length);
  });
});
