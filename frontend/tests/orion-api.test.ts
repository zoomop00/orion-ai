import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_AUDIO_RESPONSE_BYTES,
  ORION_API_BASE,
  OrionApiError,
  TEXT_TURN_PATH,
  messageForFailure,
  parseTurn,
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
      audioBase64: WAV_BASE64,
      audioFormat: "wav",
      audioMediaType: "audio/wav",
      audioBytes: WAV.length,
      state: "idle",
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
      "audioBase64",
      "audioBytes",
      "audioFormat",
      "audioMediaType",
      "responseText",
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
