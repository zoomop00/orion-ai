# ORION — MASTER ENGINEERING BRIEF v2

**Product:** ORION — Autonomous Market Intelligence  
**Roles:** User = Founder/Product Owner · ChatGPT = Lead Architect/Reviewer/Teacher · Claude Code = Primary Implementation Engineer

## READY-TO-PASTE CLAUDE CODE PROMPT

Read `ORION_MASTER_ENGINEERING_BRIEF_V2.md` completely before changing anything.

You are ORION's primary implementation engineer. The user is the founder, and ChatGPT is the lead architect/reviewer and teacher.

FIRST: audit the complete repository. Inspect Git status, files, existing architecture, Node/Python versions, dependencies, environment setup, tests, deployment, MCP/connectors and documentation. Preserve unrelated user work.

Do NOT build the whole product immediately.

Research current options before major decisions involving AI models/coding agents, market-data providers, databases, hosting, MCP servers, open-source repositories, voice providers or other important dependencies. The ecosystem changes quickly. Do not stay with a tool merely because it was previously selected. Evaluate capability, reliability, cost, security, maintenance, license, compatibility and lock-in.

OPEN-SOURCE RULE: never blindly install trending GitHub repositories. Inspect maintenance, license, security, dependencies, tests and actual usefulness first.

FIRST MILESTONE:
REAL XAUUSD DATA → DATA VALIDATION → DETERMINISTIC MARKET ANALYSIS → AI REASONING → STRUCTURED BUY/SELL/WAIT → DETERMINISTIC RISK GUARDIAN → DECISION MEMORY.

No live trading. No real-money orders. No broker execution. No unnecessary paid services.

Routine autonomous work is allowed: create/edit files, implement, refactor, test, document and fix normal bugs. Ask before destructive commands, deleting substantial data/code, major architecture changes, meaningful recurring costs, exposing secrets, changing security boundaries, or enabling live trading.

QUALITY RULES:
- No fabricated data, results or performance.
- No hardcoded secrets.
- AI output must be structured and validated.
- Risk controls are independent from the LLM.
- WAIT/NO TRADE is valid.
- ORION must be able to say “I don't know” and “insufficient confidence.”
- Run formatting, lint, typecheck and relevant tests.
- Keep decisions auditable.
- Document major architectural decisions.

The user wants to learn. For important decisions explain what we are doing, why, how it works and the trade-offs.

After each meaningful task report: changes, files, tests/results, dependencies, security considerations, limitations and next task.

Begin with the repository audit and implementation plan. Do not build the entire system at once.

---

# 1. VISION

**ORION — Autonomous Market Intelligence**

ORION should feel like the user's own futuristic AI assistant: cinematic command-center intelligence inspired by the *feeling* of JARVIS, but not a Marvel copy.

The goal is not a normal trading dashboard. The primary experience is an intelligent system that observes, analyzes, explains and helps decide.

**Core philosophy: Don't show everything. Show what matters.**

---

# 2. TEAM

### User — Founder
Product direction, budget, final approvals and learning.

### ChatGPT — Lead Architect
Architecture, current technology research, technical review, security/risk review, system design and teaching.

### Claude Code — Implementation Engineer
Coding, testing, refactoring, documentation, Git workflow and routine engineering.

**Tool-selection rule:** Claude is the current primary coding agent, not a permanent lock-in. If another tool becomes materially better for ORION, research it and change when appropriate.

---

# 3. CURRENT-FIRST RESEARCH

Before major technology decisions, research the current ecosystem.

Compare capability, reliability, cost, limits, security, maintenance, ecosystem, lock-in and ORION compatibility.

Do not endlessly research trivial decisions.

---

# 4. ARCHITECTURE

```text
                    ORION
                      │
              ┌───────▼────────┐
              │ ORCHESTRATOR   │
              └───────┬────────┘
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   MARKET DATA     RESEARCH       AI MODELS
       │              │              │
       └──────────────┼──────────────┘
                      ▼
               MARKET ANALYSIS
                      │
                      ▼
                ORION REASONING
                      │
                      ▼
                RISK GUARDIAN
                      │
               ┌──────┴──────┐
               ▼             ▼
          PAPER TRADE      WAIT
               │
               ▼
             MEMORY
               │
               ▼
           REFLECTION
```

Keep data, intelligence, risk, execution, memory, UI and observability modular.

---

# 5. INITIAL TECHNOLOGY DIRECTION

Validate choices against the current ecosystem before locking them.

Likely direction:
- Next.js + React + TypeScript
- Tailwind CSS + Framer Motion
- Python + FastAPI + Pydantic
- PostgreSQL
- Supabase if its current limits/features fit
- Redis only when justified
- Vercel as a candidate for the web layer
- Provider-agnostic AI model layer

Next.js remains a strong fit for the web interface; current Next.js 16.3 includes app-like navigation and AI-agent-oriented tooling. Vercel provides first-class Next.js deployment. Check current versions before installing.

---

# 6. MISSION 0 — FOUNDATION

- [ ] Private GitHub repository
- [ ] Add this brief
- [ ] Repository audit
- [ ] Initial architecture
- [ ] TypeScript/Python setup
- [ ] Linting/formatting
- [ ] Tests
- [ ] `.env.example`
- [ ] Secret handling
- [ ] Git workflow
- [ ] README
- [ ] `docs/DECISIONS.md`
- [ ] Basic CI
- [ ] Cheap development infrastructure

---

# 7. MISSION 1 — BRAIN

## 1A — Eyes

Start with **ONE market: XAUUSD**.

Build:
- real market data
- OHLCV
- multiple timeframes
- timestamps
- source metadata
- stale/missing/invalid data detection
- provider abstraction

## 1B — Deterministic Analysis

Build and test:
- trend
- market structure
- momentum
- volatility
- support/resistance
- ATR
- RSI
- EMA/SMA

Indicators are evidence, not the final decision.

## 1C — AI Reasoning

Give the model structured evidence and require structured output.

Example:

```json
{
  "action": "WAIT",
  "direction": "LONG",
  "confidence": 0.78,
  "risk_level": "MEDIUM",
  "reason_codes": [
    "BULLISH_TREND",
    "RESISTANCE_NEARBY",
    "INSUFFICIENT_RR"
  ]
}
```

Support:
- confidence
- uncertainty
- reason codes
- BUY/SELL/WAIT
- insufficient-data state
- model/version tracking
- prompt/version tracking

Never let free-form LLM text directly execute a trade.

## 1D — Decision Engine

Combine market state, technical evidence, research, AI reasoning, confidence, uncertainty and risk/reward.

Every decision gets an audit record.

---

# 8. MISSION 2 — MEMORY

Implement:
- short-term session context
- episodic trade/decision memory
- semantic validated lessons
- performance memory
- immutable decision journal
- historical search
- similar-setup retrieval

Never silently rewrite historical decisions.

---

# 9. MISSION 3 — RISK GUARDIAN

Independent deterministic controls:
- max risk/trade
- max daily/weekly loss
- max drawdown
- max exposure
- position size
- minimum risk/reward
- abnormal volatility
- stale data
- correlation/liquidity controls
- emergency kill switch

**AI proposes; Risk Guardian can reject.**

---

# 10. MISSION 4 — PAPER TRADING

Build:
- simulated account
- orders/fills
- spread
- slippage
- fees
- positions
- P&L
- portfolio
- journal
- complete audit trail

No real money.

---

# 11. MISSION 5 — REFLECTION

After each paper trade:
Prediction → Actual outcome → Difference → Decision-quality analysis → Lesson.

Distinguish a bad outcome from a bad decision. Do not change production strategy because of one trade.

---

# 12. MISSION 6 — BACKTESTING & VALIDATION

Build realistic historical testing with:
- fees
- spread
- slippage
- latency
- position sizing
- portfolio constraints
- drawdown
- Sharpe/Sortino
- profit factor
- expectancy
- exposure

Required validation:
- out-of-sample
- walk-forward
- multiple market regimes
- sensitivity analysis
- stress tests
- look-ahead-bias checks
- leakage checks

**Try to break the strategy before trusting it.**

---

# 13. MISSION 7 — RESEARCH

Eventually add:
- news
- economic calendar
- macro data
- sentiment

Track source, timestamp, retrieval time and data quality.

---

# 14. MISSION 8 — INTELLIGENCE TEAM

Only add when the core pipeline is stable:
- Technical Analyst
- News Analyst
- Macro Analyst
- Bull Researcher
- Bear Researcher
- Risk Manager
- Portfolio Manager
- ORION Coordinator

Every agent needs a defined responsibility and measurable value.

---

# 15. MISSION 9 — VOICE

States:

```text
IDLE → LISTENING → UNDERSTANDING → ANALYZING → SPEAKING → IDLE
```

Features:
- speech-to-text
- text-to-speech
- transcript
- interruption
- text fallback
- sensitive-command confirmation
- modular provider

Start cheap.

---

# 16. MISSION 10 — ORION INTERFACE

## Visual north star

The approved visual direction is a **clean, cinematic sci-fi command center** shown in a spacious environment—not a wall of panels.

Avoid:
- crowded trading dashboards
- generic SaaS cards
- excessive charts
- full-screen HUD clutter

Desired feeling:

> **“I have my own JARVIS.”**

The UI is ORION itself.

## ORION CORE

### Idle
- subtle breathing
- soft glow
- slow orbit
- particles

### Listening
- expands
- waveform
- reacts to user's voice
- active rings

### Thinking
- faster orbital motion
- intelligence nodes activate
- dynamic energy

### Speaking
**React to ORION's actual voice audio.**

Use audio analysis to drive:
- glow
- pulse
- orbital rings
- waveform
- particles

Quiet speech = subtle.
Normal speech = natural pulse.
Emphasis = stronger response.
Sentence end = settle.

## Dynamic information

When the user asks “Orion, analyze gold,” the interface should transform around the relevant analysis rather than display every possible metric.

Show what matters:
- market structure
- momentum
- news
- macro
- risk
- confidence
- conclusion

---

# 17. PERSONALITY

ORION should sound:
- calm
- intelligent
- professional
- concise
- transparent
- cautious under uncertainty

Example:

> “Gold is showing bullish momentum, but resistance remains nearby. I am waiting for confirmation because the current risk/reward profile is insufficient.”

Never use fake certainty or exaggerated win-rate claims.

---

# 18. WEB / MOBILE / DESKTOP

Start with a responsive web application usable on PC, laptop, tablet, iPhone and Android.

The backend/brain must be shared.

Later evaluate a desktop shell such as Tauri if it provides meaningful capabilities.

Do not build separate native apps prematurely.

---

# 19. MCP / CONNECTORS

Use MCP/connectors only where they provide real value.

Rules:
- explicit allowlist
- least privilege
- read-only where possible
- secret isolation
- review third-party servers
- version control
- documentation
- sandbox risky operations
- never bypass ORION risk controls

Potential uses:
GitHub, research, documentation, browser, databases, notifications and development tools.

---

# 20. GITHUB / OPEN-SOURCE

Never blindly install trending repositories.

Evaluate:
- maintenance
- releases
- issues
- license
- dependencies
- security
- architecture
- tests
- documentation
- compatibility

Use external projects selectively. ORION remains its own architecture.

---

# 21. SECURITY

Never commit:
- API keys
- passwords
- broker credentials
- tokens
- certificates

Use environment variables/secrets.

Eventually implement:
- authentication
- authorization
- validation
- rate limiting
- secure headers
- audit logs
- secret scanning
- dependency scanning
- backups

---

# 22. OBSERVABILITY

Track:
- request IDs
- analysis IDs
- trade IDs
- model calls
- latency
- errors
- provider health
- risk rejections
- paper trades
- cost

System health should show:
AI, market data, news, memory, risk guardian, execution mode, voice and database status.

---

# 23. COST STRATEGY

Start cheap:
- Claude Pro only when heavy coding needs it
- cheap/free hosting
- cheap/free database
- low-cost/free development market data
- native/browser voice

Upgrade only when a real limit, reliability, performance, security or usage requirement justifies it.

---

# 24. TESTING & CI/CD

Critical logic needs unit tests.

Integration tests for:
- market data
- database
- AI
- memory
- paper execution

E2E tests for:
- UI
- authentication
- analysis
- voice
- paper trading

CI should run formatting, lint, typecheck, tests, build and security/dependency checks.

Never remove tests to make CI pass.

---

# 25. FEATURE FLAGS

Examples:

```text
VOICE_ENABLED
PAPER_TRADING_ENABLED
AUTONOMOUS_SCAN_ENABLED
NEWS_ANALYSIS_ENABLED
LIVE_EXECUTION_ENABLED
```

`LIVE_EXECUTION_ENABLED` is false by default.

---

# 26. LIVE TRADING GATE

LIVE TRADING IS LOCKED.

Before considering activation:
- long paper-trading period
- out-of-sample validation
- walk-forward validation
- stress testing
- verified risk controls
- verified kill switch
- broker integration tests
- credential isolation
- monitoring
- alerts
- manual override
- explicit human approval

If ever approved: tiny exposure → monitor → validate → gradually scale.

---

# 27. PHASES

1. Foundation
2. ORION Brain
3. Memory
4. Risk Guardian
5. Paper Trading
6. Backtesting & Validation
7. Research Intelligence
8. Voice
9. Cinematic Interface
10. Production Hardening
11. Live Trading Review

---

# 28. CURRENT TASK

**Mission 0 → Mission 1A**

First Claude must audit the repository.

Then build only:

```text
REAL XAUUSD DATA
        ↓
DATA VALIDATION
        ↓
MARKET STATE
```

Do not jump ahead to live trading or a giant multi-agent system.

---

# 29. DAILY LOOP

```text
UNDERSTAND
↓
RESEARCH CURRENT OPTIONS
↓
INSPECT
↓
PLAN
↓
IMPLEMENT
↓
TEST
↓
REVIEW
↓
DOCUMENT
↓
TEACH
↓
NEXT
```

---

# 30. GOLDEN RULE

## BRAIN FIRST.
## BODY SECOND.
## HANDS LAST.

Brain = intelligence  
Memory = experience  
Voice = personality  
UI = body  
Risk Guardian = safety  
Paper trading = training  
Live execution = earned privilege

Build ORION seriously.
Test it brutally.
Research continuously.
Teach the founder.
Replace tools when better tools genuinely emerge.

**ORION should become better because we measure and improve it—not because we assume the AI is always right.**
