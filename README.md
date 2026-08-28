# ORION — Autonomous Market Intelligence

ORION observes real market data, validates it, runs deterministic technical analysis,
asks an AI model to reason over the evidence, and passes the result through an
independent Risk Guardian before recording an auditable decision. It never places
a trade.

**Current milestone (Mission 0 → Mission 1):**

```
REAL XAUUSD DATA → DATA VALIDATION → DETERMINISTIC MARKET ANALYSIS
    → AI REASONING → STRUCTURED BUY/SELL/WAIT → RISK GUARDIAN → DECISION MEMORY
```

No live trading. No broker integration. No real-money execution anywhere in this
codebase.

See `ORION_MASTER_ENGINEERING_BRIEF_V2.md` for the full product vision and roadmap,
and `docs/DECISIONS.md` for why specific technical choices were made.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) for dependency/environment management
- Node.js 20+ and npm — only for the `frontend/` interface prototype

## Setup

```bash
uv sync
cp .env.example .env
# fill in ANTHROPIC_API_KEY and TWELVE_DATA_API_KEY in .env
```

## Running

```bash
uv run orion analyze XAUUSD
```

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # typecheck
```

## The voice backend (Mission 10, Phase 2)

`src/orion/interface/` is a thin HTTP adapter over the Mission 9 voice runtime.
It is **localhost-only and unauthenticated**. Run it with:

```bash
uv run python -c "from orion.interface.app import run; run()"
# serves on http://127.0.0.1:8000 — interactive docs at /docs
```

`run()` takes no `host` argument and always binds `127.0.0.1`. **Do not expose
this server beyond loopback** — there is no authentication, no rate limiting and
no TLS, and anyone who can reach it can spend your Deepgram credit. Authentication
and production hardening are deferred; see `docs/DECISIONS.md`.

| Endpoint | Body | Purpose |
| --- | --- | --- |
| `GET /health` | — | Liveness, plus whether a Deepgram key is configured |
| `POST /v1/voice/turn` | raw audio bytes, `Content-Type: audio/*` | Speak to ORION |
| `POST /v1/voice/turn/text` | `{"text": "..."}` | Same turn, skipping speech-to-text |

Both turn endpoints return the transcript, ORION's reply, and the untranscoded
WAV audio as base64. `DEEPGRAM_API_KEY` must be set in `.env` for either to work.
Nothing is persisted: no database, no files, no request logging.

```bash
curl -s localhost:8000/v1/voice/turn/text \
  -H 'Content-Type: application/json' -d '{"text":"hello orion"}'
```

## The ORION interface (Mission 10, Phases 1, 3 and 4)

`frontend/` holds ORION's face: a full-screen cinematic core with four
presentation states — idle, listening, thinking, speaking — a single command
line, and a microphone. Both really talk to the voice backend above, and the
core's waveform is driven by real audio.

**To use it, run both halves.** In one terminal:

```bash
uv run python -c "from orion.interface.app import run; run()"   # 127.0.0.1:8000
```

In another:

```bash
cd frontend
npm install
npm run dev        # then open http://localhost:3000
```

Type a command and press Enter, **or press the microphone button and speak**.
ORION goes to **thinking** for exactly as long as the request takes, then to
**speaking** for exactly as long as the returned WAV actually plays, then back
to **idle**. `DEEPGRAM_API_KEY` must be set in `.env` or the backend answers
with a calm "voice is not configured" and the interface returns to idle.

The microphone is requested only when you press the button, and every track is
released the moment you stop — press again to send, or let the 30-second
ceiling (Mission 9's `MAX_RECORDING_SECONDS`) stop it for you. While you speak,
the core's waveform is driven by a real `AnalyserNode` reading a smoothed RMS
envelope of your voice; while ORION replies, the same envelope follows its
actual speech. Nothing is stored: no recording is kept, written or replayed, no
history, no cookies, no browser storage, no logging of what you typed or said.
A browser with no microphone, or a refused permission, produces one calm
sentence and an interface that is immediately usable again.

```bash
npm run lint       # eslint
npm run typecheck  # tsc --noEmit
npm test           # vitest
npm run build      # production build
```

The four states can also be switched with the development controls at the
bottom of the screen, which are disabled while a turn is running.

## Project layout

```
src/orion/
    config.py        # settings — API keys, AI model + effort, data provider, DB path
    data/             # market data provider abstraction + Twelve Data implementation + validation
    analysis/         # deterministic indicators and market structure (evidence, not decisions)
    reasoning/        # AI reasoning provider abstraction + Claude implementation + structured schemas
    risk/             # independent deterministic Risk Guardian (can veto the AI's proposal)
    memory/           # append-only SQLite decision journal
    pipeline.py       # wires the above into one auditable run
    cli.py            # `orion analyze <symbol>` entrypoint

    interface/        # Mission 10 Phase 2 - localhost HTTP adapter over the voice runtime
    voice/            # Mission 9 - STT/TTS providers, audio I/O, the voice state machine

frontend/             # Mission 10 Phases 1 + 3 + 4 - the ORION interface (Next.js)
    src/state/        # typed presentation states (NOT a second voice state machine)
    src/components/   # the ORION core and the command surface it sits on
    src/lib/          # core layout data, the API client, and four boundaries:
                      #   playback, microphone capture, Web Audio analysis,
                      #   and the pure RMS envelope that drives the waveform
    src/hooks/        # the turn lifecycle, and Web Animations tempo control
```

Every provider (market data, AI reasoning) sits behind an abstract interface so it
can be replaced without touching the pipeline. The AI model identifier is
configuration (`ANTHROPIC_MODEL` in `.env`), never hardcoded in application code.
