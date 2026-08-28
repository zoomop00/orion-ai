import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACCEPTED_RECORDING_MEDIA_TYPES,
  AUDIO_TURN_PATH,
  MAX_AUDIO_REQUEST_BYTES,
  MAX_AUDIO_RESPONSE_BYTES,
  ORION_API_BASE,
  OrionApiError,
  TEXT_TURN_PATH,
  messageForFailure,
  parseTurn,
  requestAudioTurn,
  requestTextTurn,
  type OrionFailure,
} from "@/lib/orion-api";

import { WAV, WAV_BASE64, turnPayload } from "./support/audio-harness";

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("requestTextTurn — the wire", () => {
  it("posts JSON to the Phase 2 text endpoint on loopback", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));

    await requestTextTurn("hello orion");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/v1/voice/turn/text");
    expect(url).toBe(`${ORION_API_BASE}${TEXT_TURN_PATH}`);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ text: "hello orion" }));
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("never lets a cookie or stored credential ride along", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));

    await requestTextTurn("hello");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("omit");
    expect(init.mode).toBe("cors");
    expect(init.cache).toBe("no-store");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(JSON.stringify(init.headers)).not.toMatch(/authorization|api[_-]?key|bearer/i);
  });

  it("sends the text verbatim, including hostile content", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));
    const hostile = '<script>alert(1)</script> {{7*7}} \'; DROP TABLE x; --';

    await requestTextTurn(hostile);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ text: hostile });
  });

  it("parses a successful turn into the interface's own vocabulary", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));

    const turn = await requestTextTurn("hello orion");

    expect(turn).toEqual({
      transcript: "hello orion",
      responseText: "Hello. I'm ORION.",
      spoken: true,
      audioBase64: WAV_BASE64,
      audioFormat: "wav",
      audioMediaType: "audio/wav",
      audioBytes: WAV.length,
      state: "idle",
      // Phase 5.3: an ordinary turn carries no analysis. Asserted as part of
      // the exact shape rather than separately, so the field cannot start
      // arriving unnoticed on turns that should not have one.
      analysis: null,
    });
  });

  it("bounds the wait with an abort controller rather than hanging forever", async () => {
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      expect(init.signal).toBeInstanceOf(AbortSignal);
      return Promise.resolve(jsonResponse(turnPayload()));
    });

    await requestTextTurn("hello");
    expect(fetchMock).toHaveBeenCalled();
  });

  it("aborts when the caller's signal aborts", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        }),
    );

    const pending = requestTextTurn("hello", { signal: controller.signal });
    controller.abort();

    await expect(pending).rejects.toMatchObject({ failure: "cancelled" });
  });
});

describe("requestTextTurn — failures", () => {
  it.each([
    [400, "rejected"],
    [409, "rejected"],
    [413, "rejected"],
    [415, "rejected"],
    [422, "rejected"],
    [500, "server"],
    [502, "upstream"],
    [503, "unavailable"],
  ])("maps HTTP %i to the %s failure", async (status, failure) => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "some_code", detail: "server side detail" }, status),
    );

    await expect(requestTextTurn("hello")).rejects.toMatchObject({ failure });
  });

  it("never surfaces the server's own error text", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "synthesis_failed", detail: "Deepgram said no" }, 502),
    );

    const error = await requestTextTurn("hello").catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(OrionApiError);
    const rendered = `${(error as Error).message} ${messageForFailure(
      (error as OrionApiError).failure,
    )}`;
    expect(rendered).not.toMatch(/Deepgram/i);
    expect(rendered).not.toMatch(/synthesis_failed/);
    expect(rendered).not.toMatch(/said no/);
  });

  it("turns a network or CORS rejection into `unreachable`", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(requestTextTurn("hello")).rejects.toMatchObject({ failure: "unreachable" });
  });

  it("turns unparseable JSON into `malformed`", async () => {
    fetchMock.mockResolvedValue(new Response("<html>not json</html>", { status: 200 }));

    await expect(requestTextTurn("hello")).rejects.toMatchObject({ failure: "malformed" });
  });

  it("carries no detail on the error object itself", () => {
    const error = new OrionApiError("upstream");

    expect(error.message).toBe("upstream");
    expect(error.name).toBe("OrionApiError");
  });

  it("has a calm, frontend-authored sentence for every failure", () => {
    const failures: OrionFailure[] = [
      "unreachable",
      "unavailable",
      "rejected",
      "upstream",
      "server",
      "malformed",
      "cancelled",
    ];
    for (const failure of failures) {
      const message = messageForFailure(failure);
      expect(message.length).toBeGreaterThan(10);
      expect(message).toMatch(/\.$/);
      expect(message).not.toMatch(/error|exception|traceback|http|\d{3}/i);
    }
  });
});

describe("parseTurn — the response is not trusted", () => {
  it("accepts exactly the Phase 2 shape", () => {
    expect(() => parseTurn(turnPayload())).not.toThrow();
  });

  it.each([
    ["null", null],
    ["an array", []],
    ["a string", "hello"],
    ["a number", 7],
  ])("rejects %s", (_label, value) => {
    expect(() => parseTurn(value)).toThrow(OrionApiError);
  });

  it.each([
    "transcript",
    "response_text",
    "audio_base64",
    "audio_format",
    "audio_media_type",
    "audio_bytes",
    "state",
  ])("rejects a payload missing %s", (field) => {
    const payload = turnPayload();
    delete payload[field];
    expect(() => parseTurn(payload)).toThrow(OrionApiError);
  });

  it.each([
    ["response_text", ""],
    ["audio_base64", ""],
    ["audio_format", ""],
    ["audio_media_type", ""],
  ])("rejects an empty %s", (field, value) => {
    expect(() => parseTurn(turnPayload({ [field]: value }))).toThrow(OrionApiError);
  });

  it.each([
    ["a string", "1024"],
    ["zero", 0],
    ["negative", -1],
    ["fractional", 12.5],
    ["NaN", Number.NaN],
    ["Infinity", Number.POSITIVE_INFINITY],
  ])("rejects audio_bytes that is %s", (_label, value) => {
    expect(() => parseTurn(turnPayload({ audio_bytes: value }))).toThrow(OrionApiError);
  });

  it("rejects audio metadata larger than the browser ceiling", () => {
    expect(() =>
      parseTurn(turnPayload({ audio_bytes: MAX_AUDIO_RESPONSE_BYTES + 1 })),
    ).toThrow(OrionApiError);
  });

  it("rejects a base64 length that disagrees with audio_bytes", () => {
    expect(() => parseTurn(turnPayload({ audio_bytes: WAV.length - 3 }))).toThrow(OrionApiError);
    expect(() => parseTurn(turnPayload({ audio_base64: `${WAV_BASE64}AAAA` }))).toThrow(
      OrionApiError,
    );
  });

  it("rejects non-string text fields", () => {
    expect(() => parseTurn(turnPayload({ transcript: 42 }))).toThrow(OrionApiError);
    expect(() => parseTurn(turnPayload({ state: { nested: true } }))).toThrow(OrionApiError);
  });

  it("rejects an absurdly long text field", () => {
    expect(() => parseTurn(turnPayload({ transcript: "a".repeat(8_001) }))).toThrow(OrionApiError);
  });

  it("ignores unexpected extra fields rather than surfacing them", () => {
    const turn = parseTurn(turnPayload({ injected_html: "<script>alert(1)</script>" }));

    expect(Object.keys(turn).sort()).toEqual([
      "analysis",
      "audioBase64",
      "audioBytes",
      "audioFormat",
      "audioMediaType",
      "responseText",
      "spoken",
      "state",
      "transcript",
    ]);
  });

  it("keeps hostile transcript content intact, as data", () => {
    const hostile = "<script>alert(document.cookie)</script>";
    const turn = parseTurn(turnPayload({ transcript: hostile }));

    expect(turn.transcript).toBe(hostile);
  });
});

describe("requestAudioTurn — the spoken wire", () => {
  const recording = (bytes = 2048, type = "audio/webm"): Blob =>
    new Blob([new Uint8Array(bytes)], { type });

  it("posts the raw recording to the Phase 2 audio endpoint on loopback", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));

    await requestAudioTurn(recording());

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/v1/voice/turn");
    expect(url).toBe(`${ORION_API_BASE}${AUDIO_TURN_PATH}`);
    expect(init.method).toBe("POST");
    // Raw bytes, not multipart: the backend reads the body directly.
    expect(init.body).toBeInstanceOf(Blob);
    expect(init.headers).toEqual({ "Content-Type": "audio/webm" });
  });

  it("carries the same credential and cache posture as the typed turn", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));

    await requestAudioTurn(recording());

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("omit");
    expect(init.mode).toBe("cors");
    expect(init.cache).toBe("no-store");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(JSON.stringify(init.headers)).not.toMatch(/authorization|api[_-]?key|bearer/i);
  });

  it("validates the response exactly as the typed turn does", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ transcript: "hi", nonsense: true }));

    await expect(requestAudioTurn(recording())).rejects.toMatchObject({ failure: "malformed" });
  });

  it("classifies a refused recording by status, never by response text", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "unsupported_media_type", detail: "tell the user this" }, 415),
    );

    await expect(requestAudioTurn(recording())).rejects.toMatchObject({ failure: "rejected" });
    expect(messageForFailure("rejected")).not.toMatch(/tell the user this/);
  });

  it("refuses to put anything but its own media types on the wire", async () => {
    // `audio/webm;codecs=opus` is exactly what `MediaRecorder` labels its own
    // output with, and it is refused here on purpose: the header value must
    // be a literal from `orion-api.ts`, not a string the platform composed.
    // (Case is not in the list because `Blob` lower-cases `type` itself, so
    // "AUDIO/WEBM" is genuinely one of ours by the time it arrives.)
    for (const hostile of [
      "text/html",
      "audio/wav; charset=utf-8",
      "audio/webm;codecs=opus",
      "",
    ]) {
      await expect(requestAudioTurn(recording(2048, hostile))).rejects.toMatchObject({
        failure: "rejected",
      });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts every media type the microphone boundary can produce", async () => {
    fetchMock.mockResolvedValue(jsonResponse(turnPayload()));

    for (const type of ACCEPTED_RECORDING_MEDIA_TYPES) {
      fetchMock.mockResolvedValue(jsonResponse(turnPayload()));
      await expect(requestAudioTurn(recording(1024, type))).resolves.toBeTruthy();
    }
  });

  it("refuses an empty recording before making a request", async () => {
    await expect(requestAudioTurn(recording(0))).rejects.toMatchObject({ failure: "rejected" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a recording larger than the server would accept anyway", async () => {
    // The size is what is checked, so a sparse Blob of the right length is
    // the honest fixture; allocating 25 MB to prove a `>` would not.
    const oversized = { size: MAX_AUDIO_REQUEST_BYTES + 1, type: "audio/webm" } as Blob;

    await expect(requestAudioTurn(oversized)).rejects.toMatchObject({ failure: "rejected" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("mirrors the server's own body ceiling", () => {
    expect(MAX_AUDIO_REQUEST_BYTES).toBe(25 * 1024 * 1024);
  });

  it("reports an unreachable backend without leaking the platform's error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch: ECONNREFUSED 127.0.0.1:8000"));

    let caught: unknown;
    try {
      await requestAudioTurn(recording());
    } catch (cause) {
      caught = cause;
    }

    expect(caught).toBeInstanceOf(OrionApiError);
    expect((caught as OrionApiError).message).toBe("unreachable");
    expect(messageForFailure((caught as OrionApiError).failure)).not.toMatch(/ECONNREFUSED/);
  });

  it("is cancellable, like every other request", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation(() => {
      controller.abort();
      return Promise.reject(new DOMException("aborted", "AbortError"));
    });

    await expect(
      requestAudioTurn(recording(), { signal: controller.signal }),
    ).rejects.toMatchObject({ failure: "cancelled" });
  });
});

describe("parseTurn — the optional analysis", () => {
  const analysisPayload = (): Record<string, unknown> => ({
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
  });

  it("is null when the server omits the field entirely", () => {
    // Every Phase 2/3 response looked exactly like this, which is what makes
    // the new field backwards compatible rather than a breaking change.
    expect(parseTurn(turnPayload()).analysis).toBeNull();
  });

  it("is null when the server sends it explicitly as null", () => {
    expect(parseTurn(turnPayload({ analysis: null })).analysis).toBeNull();
  });

  it("carries a valid analysis through onto the turn", () => {
    const turn = parseTurn(turnPayload({ analysis: analysisPayload() }));

    expect(turn.analysis).not.toBeNull();
    expect(turn.analysis?.symbol).toBe("XAUUSD");
    expect(turn.analysis?.conclusion.action).toBe("wait");
    expect(turn.analysis?.risk.availability).toBe("no_portfolio_store");
    expect(turn.analysis?.news.status).toBe("not_connected");
  });

  it("fails the whole turn when the analysis is malformed, rather than dropping it", () => {
    // Fail-closed and whole: a half-populated report cannot be told apart
    // from "ORION found nothing", and only one of those would be true.
    for (const broken of [
      { ...analysisPayload(), confidence: 42 },
      { ...analysisPayload(), symbol: "<script>" },
      { ...analysisPayload(), timeframe: "hourly" },
      { ...analysisPayload(), structure: null },
      { ...analysisPayload(), news: { status: "connected", detail: "x" } },
      "not an object",
      42,
      [],
    ]) {
      expect(() => parseTurn(turnPayload({ analysis: broken }))).toThrow(OrionApiError);
    }
  });

  it("reports a malformed analysis in this module's own failure vocabulary", () => {
    let caught: unknown;
    try {
      parseTurn(turnPayload({ analysis: { ...analysisPayload(), symbol: "LEAKED-SECRET-abc" } }));
    } catch (cause) {
      caught = cause;
    }

    expect(caught).toBeInstanceOf(OrionApiError);
    expect((caught as OrionApiError).failure).toBe("malformed");
    // The analysis parser's own error, and the offending value, stop here.
    expect((caught as OrionApiError).message).toBe("malformed");
    expect(String(caught)).not.toContain("LEAKED");
    expect(String(caught)).not.toContain("OrionAnalysisError");
  });

  it("still validates every other field when an analysis is present", () => {
    expect(() =>
      parseTurn(turnPayload({ analysis: analysisPayload(), audio_bytes: -1 })),
    ).toThrow(OrionApiError);
  });
});
