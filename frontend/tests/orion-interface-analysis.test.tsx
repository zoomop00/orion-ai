import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrionInterface } from "@/components/OrionInterface";
import { ORION_VISUAL_STATES } from "@/state/orion-visual-state";

import { analysisPayload } from "./support/analysis-fixture";
import { installAudioHarness, turnPayload, type AudioHarness } from "./support/audio-harness";

/**
 * The analysis arriving on screen, through the real turn path.
 *
 * Phase 5.3 proved a validated analysis reaches the hook and stops there.
 * This is the other end of that: a turn goes out over `fetch`, the response
 * carries an `analysis`, `parseAnalysis` validates it for real on the way in,
 * and the deck appears beneath the core. Nothing is handed a pre-parsed
 * object -- the payloads here are backend-shaped, so a change that broke
 * validation would fail here rather than quietly render a fixture.
 *
 * The other half of the point is what did *not* change. Same endpoint, same
 * four visual states, same single command surface, same reducer. The deck is
 * a consequence of a turn, not a mode the interface can be put into.
 */

let harness: AudioHarness;
let fetchMock: ReturnType<typeof vi.fn>;

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const deck = () => screen.queryByTestId("orion-analysis");
const stateOf = (): string | null =>
  screen.getByTestId("orion-interface").getAttribute("data-state");

const runTurn = async (text = "analyze gold") => {
  fireEvent.change(screen.getByTestId("orion-command-input"), { target: { value: text } });
  fireEvent.submit(screen.getByTestId("orion-command"));
};

/**
 * "The browser finished making sound." The harness resolves `play()` but
 * never fires `ended` on its own, which is deliberate -- Phase 3 built these
 * tests so that nothing advances on a timer. A turn stays busy until real
 * playback ends, so a second turn cannot start until this is called.
 */
const endSpeaking = async () => {
  await act(async () => {
    harness.endPlayback();
    await Promise.resolve();
  });
  await waitFor(() => expect(stateOf()).toBe("idle"));
};

beforeEach(() => {
  harness = installAudioHarness();
  fetchMock = vi.fn(() => Promise.resolve(jsonResponse(turnPayload())));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(async () => {
  cleanup();
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  harness.restore();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the deck exists exactly when an analysis does", () => {
  it("shows nothing at rest, before any turn has run", () => {
    render(<OrionInterface />);
    expect(deck()).toBeNull();
  });

  it("shows nothing after an ordinary conversational turn", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload())));
    render(<OrionInterface />);

    await runTurn("hello orion");
    await waitFor(() => expect(screen.getByTestId("orion-response").textContent).toBeTruthy());

    // `analysis: null` is what most turns carry, and it must render as an
    // absent deck rather than as an empty one.
    expect(deck()).toBeNull();
  });

  it("draws the analysis when a turn produces one", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))),
    );
    render(<OrionInterface />);

    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());

    expect(screen.getByTestId("orion-analysis-target").textContent).toContain("XAUUSD");
    expect(screen.getByTestId("orion-analysis-rationale").textContent).toContain(
      "risk/reward is insufficient",
    );
    expect(screen.getByTestId("orion-capability-news").textContent).toContain("NOT CONNECTED");
    expect(screen.getByTestId("orion-risk-headline").textContent).toBe("NOT AVAILABLE");
  });

  it("clears the previous analysis the moment a new turn starts", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))),
    );
    render(<OrionInterface />);

    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());
    await endSpeaking();
    // The report is still there once ORION has stopped speaking -- it belongs
    // to the answer, not to the moment of delivering it.
    expect(deck()).not.toBeNull();

    // A stale report left on screen while ORION works on the next question
    // would be the most dangerous thing this interface could do.
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload())));
    await runTurn("and now?");
    await waitFor(() => expect(stateOf()).toBe("thinking"));
    expect(deck()).toBeNull();
  });

  it("clears the analysis when a turn fails", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))),
    );
    render(<OrionInterface />);

    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());
    await endSpeaking();

    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ error: "x" }, 500)));
    await runTurn("again");

    await waitFor(() => expect(screen.getByTestId("orion-error")).toBeTruthy());
    expect(deck()).toBeNull();
  });

  it("refuses the whole turn, deck included, when the analysis is malformed", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        jsonResponse(turnPayload({ analysis: analysisPayload({ confidence: 42 }) })),
      ),
    );
    render(<OrionInterface />);

    await runTurn();
    await waitFor(() => expect(screen.getByTestId("orion-error")).toBeTruthy());

    // Fail-closed, all the way to the screen: no partial deck, and a calm
    // sentence that names nothing from the payload.
    expect(deck()).toBeNull();
    const message = screen.getByTestId("orion-error").textContent ?? "";
    expect(message).toBe("ORION returned something unexpected.");
    expect(message).not.toMatch(/confidence|42|XAUUSD|analysis/i);
  });
});

describe("the interface it grew into is still the interface", () => {
  beforeEach(() => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))),
    );
  });

  it("keeps the core on screen and in the same state vocabulary", async () => {
    render(<OrionInterface />);
    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());

    // The core is still there, still the subject, and still one of exactly
    // four states. An analysis is a result of a turn, not a fifth thing ORION
    // can be doing.
    const core = screen.getByTestId("orion-core");
    expect(core).toBeTruthy();
    expect(ORION_VISUAL_STATES).toContain(core.getAttribute("data-state"));
    expect(ORION_VISUAL_STATES).toHaveLength(4);
  });

  it("adds no second command surface, chart or table", async () => {
    const { container } = render(<OrionInterface />);
    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());

    // The Phase 1 guarantee, re-checked with a full report on screen: this is
    // where an interface would ordinarily start growing into a dashboard.
    expect(container.querySelectorAll("form")).toHaveLength(1);
    expect(container.querySelectorAll("input")).toHaveLength(1);
    expect(container.querySelectorAll("table")).toHaveLength(0);
    expect(container.querySelectorAll("canvas")).toHaveLength(0);
    expect(container.querySelectorAll("textarea, select")).toHaveLength(0);
  });

  it("lets the centre row grow so the report is scrolled to, never clipped", async () => {
    render(<OrionInterface />);
    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());

    // `.shell` keeps `overflow: hidden`; the centre row opts out of its
    // `min-height: 0` instead, which is what turns a clipped deck into a
    // taller page. The attribute is the hook the stylesheet keys on.
    const centre = screen.getByTestId("orion-analysis").parentElement;
    expect(centre?.getAttribute("data-analysis")).toBe("true");
  });

  it("still sends one request to the one endpoint", async () => {
    render(<OrionInterface />);
    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://127.0.0.1:8000/v1/voice/turn/text",
    );
  });

  it("keeps the microphone and dev controls exactly where they were", async () => {
    render(<OrionInterface />);
    await runTurn();
    await waitFor(() => expect(deck()).not.toBeNull());

    expect(screen.getByTestId("orion-mic")).toBeTruthy();
    for (const state of ORION_VISUAL_STATES) {
      expect(screen.getByTestId(`state-${state}`)).toBeTruthy();
    }
  });
});
