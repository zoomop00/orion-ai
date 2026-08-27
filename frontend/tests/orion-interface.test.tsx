import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { OrionInterface } from "@/components/OrionInterface";
import { ORION_VISUAL_STATES, profileFor } from "@/state/orion-visual-state";

afterEach(cleanup);

const stateOf = (testId: string): string | null =>
  screen.getByTestId(testId).getAttribute("data-state");

describe("OrionInterface", () => {
  it("starts idle", () => {
    render(<OrionInterface />);
    expect(stateOf("orion-interface")).toBe("idle");
    expect(stateOf("orion-core")).toBe("idle");
    expect(screen.getByTestId("orion-status").textContent).toContain(
      profileFor("idle").status,
    );
  });

  it("renders the correct state everywhere when a dev control is used", () => {
    render(<OrionInterface />);

    for (const state of ORION_VISUAL_STATES) {
      fireEvent.click(screen.getByTestId(`state-${state}`));

      expect(stateOf("orion-interface")).toBe(state);
      expect(stateOf("orion-core")).toBe(state);

      const readout = screen.getByTestId("orion-status").textContent ?? "";
      expect(readout).toContain(profileFor(state).label);
      expect(readout).toContain(profileFor(state).status);

      expect(screen.getByTestId(`state-${state}`).getAttribute("aria-pressed")).toBe("true");
    }
  });

  it("walks the cinematic sequence with the advance control", () => {
    render(<OrionInterface />);
    const advance = screen.getByTestId("state-advance");

    for (const expected of ["listening", "thinking", "speaking", "idle"]) {
      fireEvent.click(advance);
      expect(stateOf("orion-interface")).toBe(expected);
    }
  });

  it("toggles listening from the microphone affordance without claiming a microphone", () => {
    render(<OrionInterface />);
    const mic = screen.getByTestId("orion-mic");

    expect(mic.getAttribute("aria-pressed")).toBe("false");
    expect(mic.getAttribute("aria-label")).toMatch(/no microphone is connected/i);

    fireEvent.click(mic);
    expect(stateOf("orion-interface")).toBe("listening");
    expect(mic.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(mic);
    expect(stateOf("orion-interface")).toBe("idle");
  });

  it("announces state changes politely rather than silently", () => {
    render(<OrionInterface />);
    expect(screen.getByTestId("orion-status").getAttribute("aria-live")).toBe("polite");
  });

  it("shows no market data, numbers, or dashboard furniture", () => {
    const { container } = render(<OrionInterface />);

    expect(container.querySelectorAll("table")).toHaveLength(0);
    expect(container.querySelectorAll("canvas")).toHaveLength(0);
    expect(container.querySelectorAll("input")).toHaveLength(0);
    expect(container.querySelectorAll("form")).toHaveLength(0);

    // No prices, percentages or ticker symbols anywhere in the rendered text.
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\d+\.\d+/);
    expect(text).not.toMatch(/[$€£]|XAU|USD|%/);
  });

  it("states honestly what the prototype is not connected to", () => {
    render(<OrionInterface />);
    expect(screen.getByText(/no microphone, no network, no stored data/i)).toBeTruthy();
  });
});
