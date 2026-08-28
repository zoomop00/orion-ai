import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOrionTurn } from "@/hooks/use-orion-turn";
import { ORION_VISUAL_STATES } from "@/state/orion-visual-state";

import { installAudioHarness, turnPayload, type AudioHarness } from "./support/audio-harness";
import { installMediaHarness, type MediaHarness } from "./support/media-harness";

/**
 * The analysis reaching the hook, and stopping there.
 *
 * Phase 5.3 carries a validated analysis from the API boundary to the turn
 * controller and no further — nothing renders it until Phase 5.4. So this is
 * tested through `useOrionTurn` directly rather than through the DOM: there
 * is deliberately nothing in the DOM to look at, and asserting on the hook is
 * how that stays true rather than becoming an untested field.
 *
 * The other half of the point is what did *not* change. There are still four
 * visual states, the reducer still owns the lifecycle, and an analysis is a
 * result of the `thinking` -> `speaking` sequence rather than a fifth thing
 * ORION can be doing.
 */

let audio: AudioHarness;
let media: MediaHarness;
let fetchMock: ReturnType<typeof vi.fn>;

// A fresh Response per call: a body can only be read once, so a mock that
// resolved one shared instance would make the second turn of a two-turn test
// fail for a reason that has nothing to do with ORION.
const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function analysisPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    analysis_id: "analysis-XAUUSD-1h-2026-08-28T05:00:00+00:00",
    symbol: "XAUUSD",
    timeframe: "1h",
    as_of: "2026-08-28T05:00:00Z",
    analyzed_at: "2026-08-28T05:37:42.115557Z",
    last_price: 2520.6,
    bar_count: 120,
    structure: {
      trend: "bullish",
      volatility: "low",
      nearest_support: null,
      nearest_resistance: null,
      swing_high_count: 0,
      swing_low_count: 0,
    },
    momentum: {
      rsi: 62.5,
      ema_fast: 2510.4,
      ema_slow: 2495.3,
      sma_slow: 2495.3,
      atr: 2.5,
      rsi_length: 14,
      ema_fast_length: 20,
      ema_slow_length: 50,
    },
    news: { status: "not_connected", detail: "No news source is connected in this build." },
    macro: { status: "not_connected", detail: "No macro source is connected in this build." },
    risk: {
      availability: "no_portfolio_store",
      outcome: null,
      reason_codes: [],
      failed_controls: [],
      portfolio_as_of: null,
    },
    confidence: 0.55,
    risk_level: "medium",
    conclusion: {
      action: "wait",
      direction: "none",
      rationale: "Nothing decisive here yet.",
      reason_codes: ["all_rules_passed"],
    },
    ...overrides,
  };
}

beforeEach(() => {
  audio = installAudioHarness();
  media = installMediaHarness();
  fetchMock = vi.fn(() => Promise.resolve(jsonResponse(turnPayload())));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(async () => {
  cleanup();
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  media.restore();
  audio.restore();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Run one typed turn to completion and return the controller. */
async function runTypedTurn(payload: Record<string, unknown>) {
  fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(payload)));
  const { result } = renderHook(() => useOrionTurn());

  await act(async () => {
    result.current.submit("analyze gold");
  });
  await waitFor(() => expect(audio.played).toHaveLength(1));
  await act(async () => {
    audio.endPlayback();
  });
  await waitFor(() => expect(result.current.busy).toBe(false));

  return result;
}

describe("the analysis reaches the turn controller", () => {
  it("starts null", () => {
    const { result } = renderHook(() => useOrionTurn());
    expect(result.current.analysis).toBeNull();
  });

  it("carries a validated analysis from a typed turn", async () => {
    const result = await runTypedTurn(turnPayload({ analysis: analysisPayload() }));

    expect(result.current.analysis).not.toBeNull();
    expect(result.current.analysis?.symbol).toBe("XAUUSD");
    expect(result.current.analysis?.conclusion.action).toBe("wait");
    expect(result.current.analysis?.news.status).toBe("not_connected");
    expect(result.current.responseText).toBe("Hello. I'm ORION.");
  });

  it("stays null for an ordinary turn", async () => {
    const result = await runTypedTurn(turnPayload());
    expect(result.current.analysis).toBeNull();
  });

  it("is exactly the object the parser produced, not a copy or a reshape", async () => {
    const result = await runTypedTurn(turnPayload({ analysis: analysisPayload() }));

    // The hook carries; it does not transform. Anything Phase 5.4 renders is
    // therefore exactly what `parseAnalysis` validated.
    expect(Object.keys(result.current.analysis ?? {}).sort()).toEqual([
      "analysisId",
      "analyzedAt",
      "asOf",
      "barCount",
      "conclusion",
      "confidence",
      "lastPrice",
      "macro",
      "momentum",
      "news",
      "risk",
      "riskLevel",
      "structure",
      "symbol",
      "timeframe",
    ]);
  });
});

describe("the microphone path uses the same parser", () => {
  it("carries an analysis from a spoken turn too", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))));
    const { result } = renderHook(() => useOrionTurn());

    await act(async () => {
      result.current.startListening();
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.visualState).toBe("listening"));

    await act(async () => {
      result.current.stopListening();
      await Promise.resolve();
    });
    await waitFor(() => expect(audio.played).toHaveLength(1));
    await act(async () => {
      audio.endPlayback();
    });
    await waitFor(() => expect(result.current.busy).toBe(false));

    // One fetch, to the audio endpoint, and the same validated shape.
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("http://127.0.0.1:8000/v1/voice/turn");
    expect(result.current.analysis?.symbol).toBe("XAUUSD");
    expect(result.current.analysis?.timeframe).toBe("1h");
  });
});

describe("a stale analysis is never left lying around", () => {
  it("clears when the next turn begins", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))));
    const { result } = renderHook(() => useOrionTurn());

    await act(async () => {
      result.current.submit("analyze gold");
    });
    await waitFor(() => expect(audio.played).toHaveLength(1));
    await act(async () => {
      audio.endPlayback();
    });
    await waitFor(() => expect(result.current.analysis).not.toBeNull());

    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload())));
    await act(async () => {
      result.current.submit("hello orion");
    });
    // Cleared the moment the new turn starts, not when its answer arrives.
    expect(result.current.analysis).toBeNull();

    await waitFor(() => expect(audio.played).toHaveLength(2));
    await act(async () => {
      audio.endPlayback();
    });
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.analysis).toBeNull();
  });

  it("clears when a turn fails", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))));
    const { result } = renderHook(() => useOrionTurn());

    await act(async () => {
      result.current.submit("analyze gold");
    });
    await waitFor(() => expect(audio.played).toHaveLength(1));
    await act(async () => {
      audio.endPlayback();
    });
    await waitFor(() => expect(result.current.analysis).not.toBeNull());

    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await act(async () => {
      result.current.submit("analyze gold");
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    expect(result.current.analysis).toBeNull();
    expect(result.current.visualState).toBe("idle");
  });

  it("clears when the microphone is armed", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))));
    const { result } = renderHook(() => useOrionTurn());

    await act(async () => {
      result.current.submit("analyze gold");
    });
    await waitFor(() => expect(audio.played).toHaveLength(1));
    await act(async () => {
      audio.endPlayback();
    });
    await waitFor(() => expect(result.current.analysis).not.toBeNull());

    await act(async () => {
      result.current.startListening();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.analysis).toBeNull();

    await act(async () => {
      result.current.stopListening();
      await Promise.resolve();
    });
    await waitFor(() => expect(audio.played).toHaveLength(2));
    await act(async () => {
      audio.endPlayback();
    });
  });
});

describe("a malformed analysis fails the whole turn", () => {
  it("never delivers a partially valid report", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload({ confidence: 42 }) }))));
    const { result } = renderHook(() => useOrionTurn());

    await act(async () => {
      result.current.submit("analyze gold");
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    expect(result.current.analysis).toBeNull();
    expect(result.current.responseText).toBeNull();
    expect(result.current.visualState).toBe("idle");
    expect(result.current.busy).toBe(false);
    expect(audio.played).toHaveLength(0);
  });

  it("shows a calm sentence that names nothing from the payload", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload({ symbol: "LEAKED-SECRET" }) }))));
    const { result } = renderHook(() => useOrionTurn());

    await act(async () => {
      result.current.submit("analyze gold");
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    const message = result.current.error ?? "";
    expect(message).toMatch(/returned something unexpected/i);
    expect(message).not.toMatch(/LEAKED|SECRET|symbol|confidence|analysis|\.py|Traceback/i);
  });
});

describe("nothing about the visual model changed", () => {
  it("still has exactly four visual states", () => {
    expect(ORION_VISUAL_STATES).toEqual(["idle", "listening", "thinking", "speaking"]);
  });

  it("an analysis turn walks the same states as any other", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(turnPayload({ analysis: analysisPayload() }))));
    audio.mode = "manual";
    const { result } = renderHook(() => useOrionTurn());
    const seen: string[] = [result.current.visualState];

    await act(async () => {
      result.current.submit("analyze gold");
    });
    seen.push(result.current.visualState);

    await waitFor(() => expect(audio.played).toHaveLength(1));
    await act(async () => {
      audio.startPlayback();
    });
    await waitFor(() => expect(result.current.visualState).toBe("speaking"));
    seen.push(result.current.visualState);

    await act(async () => {
      audio.endPlayback();
    });
    await waitFor(() => expect(result.current.visualState).toBe("idle"));
    seen.push(result.current.visualState);

    expect(seen).toEqual(["idle", "thinking", "speaking", "idle"]);
    expect(new Set(seen).size).toBeLessThanOrEqual(ORION_VISUAL_STATES.length);
  });
});
