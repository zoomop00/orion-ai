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
```

Every provider (market data, AI reasoning) sits behind an abstract interface so it
can be replaced without touching the pipeline. The AI model identifier is
configuration (`ANTHROPIC_MODEL` in `.env`), never hardcoded in application code.
