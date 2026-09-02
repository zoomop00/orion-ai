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

One analysis of XAUUSD on the 1h chart — market data, validation, deterministic
analysis, AI reasoning, the decision engine and the Risk Guardian — printed to the
terminal. It needs `ANTHROPIC_API_KEY` and `TWELVE_DATA_API_KEY`; without them it says
so and exits non-zero. **It never places an order**, and it writes nothing to disk — no
paper account is opened, so the risk panel honestly reports `no_portfolio_store`.
`orion --help` lists the commands.

## The specialists and the thesis (Mission 8)

Every analysis also runs the **Technical Analyst** — deterministic computation
over the bars the analysis already fetched, no credential, no second request,
no model call — and, when a calendar provider and an analyst are wired, the
**Macro Analyst**. Their two readings are combined into a **market thesis**,
which compares how *eventful* each domain is rather than fusing them into a
direction: ORION has run no study that would justify turning "tightening
pressure" into "bullish for gold", so it does not pretend to have one.

The thesis is **advisory and has no authority**. It is computed *after* the
proposal and the Risk Guardian's verdict already exist, it has no field that
can express an action, size, entry, stop, target or approval, and neither
`orion.decision` nor `orion.risk` can import it. `orion analyze` prints it;
nothing acts on it.

### Where it goes

```
analysis  ──▶  analysis_id  ──▶  decision ──▶ verdict ──▶ paper cycle ──▶ journal
                    │                                                       ▲
                    └────────────────▶  MarketThesis  ─────────────────────┘
```

`analysis_id` identifies **one analysis** — it is derived from the symbol, the
timeframe, the last bar's timestamp *and* the instant of the analysis, so two
runs over the same bar are two chains with two ids. It is stamped on the
proposal, the verdict and the thesis alike, and the thesis carries no reference
back to either of the other two — that is what lets it be found later without
ever pointing at the authority that reviewed it. "Which bars was this reasoned
from" is a different question with a different answer: the content-addressed
`evidence_fingerprint`, which two analyses of the same bar do share.

`orion paper SYMBOL` journals the thesis alongside the chain that authorised a
simulated trade, and `orion reflect SYMBOL` reads it back for each closed
position — printing what the specialists were reading between what ORION
predicted and what actually happened, and drawing no conclusion from the pair.
A trade whose analysis recorded no thesis says so rather than leaving a gap.

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
    analysts/         # Mission 8 - the Technical and Macro Analysts, and the synthesis seam
    thesis/           # Mission 8 - the MarketThesis schema, shared by paper/memory/reflection
    risk/             # independent deterministic Risk Guardian (can veto the AI's proposal)
    decision/         # the Decision Engine: reasoning + evidence -> a ProposedDecision
    paper/            # Mission 4 - the simulated account, execution and the forward cycle
    reflection/       # Mission 5 - what was predicted, beside what happened. No score.
    memory/           # append-only SQLite decision journal
    orchestrator/     # Mission 10 Phase 5.1 - wires the above into one auditable analysis
    cli.py            # `orion analyze|account|paper|reflect` - the console entrypoint

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
