import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  /**
   * Phase 1 and 3's microphone button previewed the listening *visual* and
   * said so in its own label. Phase 4 made it real, so the assertion that it
   * claimed no microphone would now be asserting a lie. It is replaced, not
   * dropped: the button must still announce what it is about to do, and it
   * must still come to rest when a browser cannot help. The granted-permission
   * path lives in `orion-voice-turn.test.tsx`, which has the mocks for it.
   */
  it("offers a microphone honestly, and names the permission it will ask for", () => {
    render(<OrionInterface />);
    const mic = screen.getByTestId("orion-mic");

    expect(mic.getAttribute("aria-pressed")).toBe("false");
    expect(mic.getAttribute("aria-label")).toMatch(/permission to use the microphone/i);
    expect(mic.getAttribute("aria-label")).not.toMatch(/no microphone is connected/i);
  });

  it("comes to rest with a calm sentence when the browser cannot record", async () => {
    // jsdom has no `navigator.mediaDevices`, which is exactly the shape of a
    // browser that cannot record. Nothing should throw, and nothing should
    // claim to be listening.
    render(<OrionInterface />);

    fireEvent.click(screen.getByTestId("orion-mic"));

    await waitFor(() => expect(screen.getByTestId("orion-error")).toBeTruthy());
    expect(screen.getByTestId("orion-error").textContent).toMatch(/cannot record audio/i);
    expect(stateOf("orion-interface")).toBe("idle");
    expect(screen.getByTestId("orion-mic")).toHaveProperty("disabled", false);
  });

  it("still previews every state from the development controls, with no device", () => {
    render(<OrionInterface />);

    fireEvent.click(screen.getByTestId("state-listening"));
    expect(stateOf("orion-interface")).toBe("listening");
    expect(stateOf("orion-core")).toBe("listening");
    // A previewed listening state is not a claim to be capturing anything.
    expect(screen.getByTestId("orion-mic").getAttribute("aria-pressed")).toBe("false");
  });

  it("announces state changes politely rather than silently", () => {
    render(<OrionInterface />);
    expect(screen.getByTestId("orion-status").getAttribute("aria-live")).toBe("polite");
  });

  it("shows no market data, numbers, or dashboard furniture", () => {
    const { container } = render(<OrionInterface />);

    expect(container.querySelectorAll("table")).toHaveLength(0);
    expect(container.querySelectorAll("canvas")).toHaveLength(0);
    // Phase 3 adds exactly one command surface -- one form, one input, and
    // nothing else. This assertion was "zero inputs" in Phase 1; tightening
    // it to "exactly one" keeps it doing the job it was written for, which
    // is stopping the interface from growing into a dashboard.
    expect(container.querySelectorAll("form")).toHaveLength(1);
    expect(container.querySelectorAll("input")).toHaveLength(1);
    expect(container.querySelectorAll("textarea")).toHaveLength(0);
    expect(container.querySelectorAll("select")).toHaveLength(0);
    // No chat log: the last exchange is two lines in the existing readout,
    // never an accumulating list.
    expect(container.querySelectorAll("ul, ol, li")).toHaveLength(0);
    expect(container.querySelectorAll("time")).toHaveLength(0);

    // No prices, percentages or ticker symbols anywhere in the rendered text.
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\d+\.\d+/);
    expect(text).not.toMatch(/[$€£]|XAU|USD|%/);
  });

  it("states honestly what the prototype is and is not connected to", () => {
    render(<OrionInterface />);
    // Each phase rewrites this line rather than letting it go stale. Phase 3
    // replaced Phase 1's "no network"; Phase 4 replaces Phase 3's "no
    // microphone yet", which is now false, and says what the microphone
    // actually does instead.
    expect(screen.getByText(/talks only to ORION on this machine/i)).toBeTruthy();
    const disclosure = screen.getByText(/talks only to ORION on this machine/i).textContent ?? "";
    expect(disclosure).toMatch(/microphone runs only while you are speaking a turn/i);
    expect(disclosure).toMatch(/released the moment you stop/i);
    expect(disclosure).toMatch(/nothing is recorded, stored or logged/i);
    expect(disclosure).not.toMatch(/no microphone yet/i);
  });
});
