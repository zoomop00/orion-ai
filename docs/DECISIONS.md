# ORION — Architecture Decision Log

Format: date, decision, why, alternatives considered. Entries are append-only —
superseded decisions get a new entry that says so, not an edit to the old one.

---

## 2026-08-23 — Python-only architecture for Mission 0–1

**Decision:** Build the Mission 0 → Mission 1 brain (data → validation → analysis →
AI reasoning → risk → memory) as a Python-only backend pipeline. No Next.js/React
frontend, no Vercel, no Supabase, no Redis, no Docker in this milestone.

**Why:** The brief's own golden rule is "brain first, body second, hands last."
Everything in this milestone is data/analysis/AI logic with no UI or multi-user
concerns yet — adding a frontend and hosting stack now would be unused surface
area and unnecessary cost.

**Alternatives considered:** Next.js + FastAPI from day one (rejected — premature;
nothing to display yet), TypeScript-only (rejected — Python has the stronger
data/ML/quant ecosystem for this layer).

---

## 2026-08-23 — Dependency management: uv

**Decision:** Use `uv` for Python dependency and environment management instead of
plain `pip`/`venv` or `poetry`.

**Why:** Fast, single-binary, resolves and locks reliably, and pairs naturally
with `ruff` (same tooling philosophy/vendor). Verified all selected dependencies
(`pandas`, `numpy`, `pydantic`/`pydantic-core`, `httpx`, `sqlalchemy`,
`pandas-ta-classic`, `mypy`) have working Python 3.14 wheels before locking this
in — no dependency needed a downgrade or substitution.

---

## 2026-08-23 — Market data provider: Twelve Data (behind an abstraction)

**Decision:** Use Twelve Data's free tier as the initial real XAUUSD OHLCV source,
accessed only through a `MarketDataProvider` abstract interface
(`src/orion/data/provider_base.py`).

**Why:** Free tier (8 req/min, 800/day, multiple intraday/daily intervals) is
sufficient for periodic, non-high-frequency analysis of a single symbol, at $0
cost. The abstraction means switching to TraderMade, EODHD, or another provider
later is a new implementation file, not a pipeline rewrite.

**Alternatives considered:** TraderMade, EODHD (deeper history, but paid),
OANDA (broker-tied). None needed for this milestone's scope.

---

## 2026-08-23 — Decision memory: SQLite

**Decision:** Store the decision journal in a local SQLite file via SQLAlchemy,
not Postgres/Supabase.

**Why:** Zero infrastructure, zero cost, matches "no unnecessary paid services."
The journal's access layer (`src/orion/memory/journal.py`) only exposes
create/read operations — no update or delete — so the append-only requirement is
enforced by the code, not just convention. Migrating to Postgres later is a
connection-string change since SQLAlchemy already abstracts the engine.

---

## 2026-08-23 — Technical analysis library: pandas-ta-classic

**Decision:** Use `pandas-ta-classic` for deterministic indicators (EMA, SMA,
RSI, ATR).

**Why:** The original `pandas-ta` package has an unclear maintenance future. The
`pandas-ta-classic` community fork is actively maintained, MIT-licensed, and
follows a rolling Python-version support policy (latest stable + four preceding
minors) that covers Python 3.14.

**Alternatives considered:** `bukosabino/ta` (also viable, kept as a fallback if
`pandas-ta-classic` shows a real compatibility problem during implementation),
`TA-Lib` (C-core, heavier install, not needed for this indicator set).

---

## 2026-08-23 — AI reasoning provider default: `claude-opus-5`, kept fully configurable

**Decision:** `ANTHROPIC_MODEL` defaults to `claude-opus-5`. The model identifier
is read from configuration everywhere in the codebase — no code path hardcodes a
model string. Reasoning depth (`ANTHROPIC_EFFORT`) is a **separate** configuration
value from the model identifier, since the Anthropic API exposes effort as an
independent request parameter.

**Why:** An earlier draft of this decision defaulted to `claude-sonnet-5` purely
for cost reasons. That was reconsidered: Sonnet should not be treated as a
permanent default chosen without checking what Anthropic currently recommends.
`claude-opus-5` is Anthropic's current highest-capability generally available
model and its documented default recommendation absent a specific reason to use
something else. Cost is a real, legitimate concern for ORION's AI reasoning
budget — the fix for that is lowering `ANTHROPIC_EFFORT` or switching
`ANTHROPIC_MODEL` in `.env`, not silently defaulting to a weaker model in code.

**How to change it later:** edit `ANTHROPIC_MODEL` (and optionally
`ANTHROPIC_EFFORT`) in `.env`. No code changes required. If Anthropic ships a
newer recommended model, update the default in `.env.example` and this log entry
— do not need to touch `src/orion/reasoning/claude_provider.py`.

**Alternatives considered:** `claude-sonnet-5` as default (rejected per above —
still fully supported as a config override for anyone who wants the cheaper
tier), hardcoding a model constant in code (rejected — violates "changing the
model must require only configuration changes").

---

## 2026-08-23 — Volatility-regime thresholds are documented as uncalibrated

**Decision:** `classify_volatility` (`src/orion/analysis/structure.py`) keeps its
existing `low_atr_pct=0.003` / `high_atr_pct=0.008` defaults (ATR as a percent of
last close). The implementation was not changed. Its docstring and this entry now
say explicitly what was previously undocumented: these are order-of-magnitude
engineering placeholders, not thresholds derived from a historical XAUUSD ATR%
distribution. The test (`tests/analysis/test_structure.py::test_classify_volatility_levels`)
was updated to use ATR values that land clearly inside each bucket under the
*existing* thresholds (0.1% / 0.5% / 1.0%), instead of a value (0.25%) that
happened to sit in a different bucket than the test author assumed.

**Why:** The task was to determine whether the existing semantics are reasonable,
not to pick whichever thresholds make a failing test pass. Two people could
plausibly have written 0.3%/0.8% as first-pass intraday bands, and I have no
historical ATR% distribution for XAUUSD in this repo to justify picking different
numbers — doing so would just replace one unjustified constant with another and
falsely imply the new ones were better-reasoned. Leaving the implementation
alone and documenting the gap honestly is the choice that satisfies "never claim
empirical calibration that wasn't done."

**Known limitations (tracked for Mission 6 — Backtesting & Validation):**
1. The function is timeframe-agnostic — the same 0.3%/0.8% cut points apply
   whether `indicators.atr` was computed on M15, H1, H4, or D1 bars, even
   though ATR% naturally scales with the bar interval. A fixed threshold will
   misclassify at least some timeframes.
2. No backtest or percentile analysis over real XAUUSD history has been run to
   justify the specific cut points. `VolatilityLevel` should be treated as a
   rough regime signal, not a calibrated one, until that analysis exists.

**Alternatives considered:** Changing the thresholds to match the test's
original 0.25%-as-MEDIUM assumption (rejected — equally unjustified, and would
have hidden the calibration gap instead of documenting it). Making
`classify_volatility` timeframe-aware now (deferred — real calibration data
should drive that design, not a guess made while fixing lint/test bugs).

---

## 2026-08-23 — Reasoning output split into `ReasoningVerdict` / `ReasoningOutput`

**Decision:** The AI reasoning schema (`src/orion/reasoning/schemas.py`) is two
Pydantic models, not one. `ReasoningVerdict` is exactly the JSON schema sent to
Claude as `output_format` (action, direction, confidence, risk_level,
reason_codes, insufficient_data, rationale). `ReasoningOutput(ReasoningVerdict)`
adds `model` and `prompt_version` — provenance fields populated by
`ClaudeReasoningProvider` from the API response and a module constant, never by
the model itself.

**Why:** The alternative — one schema with `model`/`prompt_version` as fields
the LLM fills in — asks Claude to self-report exactly the two fields whose only
purpose is auditing Claude: which model actually ran, and which prompt version
produced this output. A model has every incentive to answer plausibly and no
reliable way to know either fact. Splitting the schema means those two fields
are always provider-observed fact (`response.model`, a `PROMPT_VERSION`
constant bumped when the prompt changes), not model-reported claims.

**Related decisions in the same layer:**
- `insufficient_data=True` requires `action=WAIT`, enforced by a
  `model_validator` on `ReasoningVerdict` itself — so a model that claims
  insufficient data while proposing BUY/SELL fails schema validation inside
  `client.messages.parse()` and surfaces as `ReasoningProviderError`, never as
  a laundered result.
- `action` and `direction` are deliberately uncoupled (no BUY⇒LONG validator).
  The brief's own example pairs `action=WAIT` with `direction=LONG` ("biased
  long, no entry yet"), so rigid coupling would reject a legitimate output
  class. The BUY↔LONG / SELL↔SHORT pairing convention is stated in the system
  prompt instead of enforced in code — a known soft spot; hardening it into the
  validator later is a small, isolated change if it turns out to matter.
- `ReasonCode` is a closed 22-value `StrEnum` (trend/momentum/volatility/
  structure/evidence-quality groups) rather than free-text reasons, so
  "how often did X precede a bad outcome" is a query in Mission 5, not a text
  search. Every group includes an "unknown/unavailable" code so the model has
  a way to cite missing evidence honestly instead of reaching for a
  confident-sounding code when data is thin.
- Tests inject a mock `anthropic.Anthropic` client via `ClaudeReasoningProvider`'s
  `client` constructor parameter (mirroring `TwelveDataProvider`'s `client:
  httpx.Client | None`) rather than using `respx`. `respx` mocks `httpx`;
  `anthropic` 1.x is built on the separate `httpx2` package, so `respx` cannot
  intercept it — this was confirmed against the installed SDK, not assumed.

**Alternatives considered:** A single flat schema with the model self-reporting
provenance (rejected — see above). Coupling action/direction rigidly (rejected
— contradicts the brief's own WAIT+LONG example). Free-text `reason_codes`
(rejected — the founder explicitly asked for enums wherever practical, and
free text isn't queryable for the Mission 5 reflection loop).

---

## 2026-08-23 — `ReasoningVerdict` hardened: BUY/SELL now require a matching direction

**Supersedes in part:** the "related decisions" note in the entry directly
above, which described `action` and `direction` as deliberately uncoupled and
called the missing BUY↔LONG / SELL↔SHORT pairing "a known soft spot." That
entry is left unedited as a record of the reasoning at the time; this entry
records the follow-up decision to close that gap.

**Decision:** `ReasoningVerdict._check_reasoning_invariants` now enforces, on
top of the invariants already in place:
- `action=BUY` requires `direction=LONG`.
- `action=SELL` requires `direction=SHORT`.
- `action=WAIT` remains unconstrained — `direction` may be `LONG`, `SHORT`, or
  `NONE`.

Unchanged from the prior entry: `insufficient_data=True` still requires
`action=WAIT`, and `reason_codes` still may not contain duplicates. All four
invariants live in the same `model_validator`.

**Why:** The prior entry treated the BUY↔LONG / SELL↔SHORT pairing as prompt
guidance only, reasoning that rigid coupling would reject the brief's own
WAIT+LONG example. That concern only applies to `WAIT`, which stays fully
unconstrained. It does not justify leaving `BUY`+`SHORT` or `SELL`+`LONG`
schema-legal: those are self-contradictory states — a proposal to buy while
declaring a short bias, or sell while declaring a long bias — and prompt
guidance alone is not enforcement. Relying on Claude to always honor the
pairing convention meant a self-contradictory verdict could pass schema
validation and reach `ReasoningOutput`, i.e. downstream decision logic, with no
mechanism to catch it. Enforcing the pairing in the validator instead means
such a verdict fails inside `client.messages.parse()` and surfaces as
`ReasoningProviderError`, the same fail-closed path already used for
`insufficient_data`/`WAIT` violations and duplicate `reason_codes` — consistent
with this layer's existing principle that failure must never be laundered into
a plausible-looking result.

**Alternatives considered:** Leaving the pairing as prompt-only guidance
(rejected — this is exactly what created the gap being closed). Coupling `WAIT`
to a specific direction as well (rejected — `WAIT`+`LONG`/`SHORT` is a
legitimate "biased, no entry yet" result per the brief's own example, and nothing
about this change affects that).

---

## 2026-08-23 — No MCP servers, no broker integration, no live execution

**Decision:** This milestone adds no MCP server, no broker API client, and no
order-execution code path of any kind — not even a disabled/stubbed one.

**Why:** Nothing in Mission 0–1's scope (data → validation → analysis →
reasoning → risk → memory) needs MCP or execution. Per the brief's Live Trading
Gate, execution is a separately-earned milestone with its own explicit approval
gate; not building the code path at all is a stronger guarantee than building it
disabled.

---

## 2026-08-23 — Decision Engine: narrows an AI proposal, never authorizes one

**Decision:** `src/orion/decision/` (`schemas.py`, `rules.py`, `engine.py`)
combines a validated `Evidence` and a validated `ReasoningOutput` into a
`ProposedDecision` through five deterministic rules. The engine can only ever
narrow an AI proposal to `WAIT` — it never invents a `BUY`/`SELL` the AI did
not propose, and `ProposedDecision`'s own validator makes this structurally
unrepresentable: `action` may only equal `reasoning_ref.action` or `WAIT`,
and any failed rule forces `action=WAIT`. Tests live at the top-level
`tests/decision/`, not nested under `src/orion/` as an earlier sketch of the
tree suggested — `pyproject.toml`'s `testpaths = ["tests"]` wouldn't collect
tests nested under `src/`, and the wheel build (`packages = ["src/orion"]`)
would ship them.

**The rules, and why each is Decision Engine territory and not `analysis/`'s
or Risk Guardian's:**
1. `ai_insufficient_data_requires_wait` / `proposal_is_internally_consistent`
   — restate two invariants `ReasoningVerdict` already enforces at
   construction time. Deliberately redundant: a `ReasoningOutput` reaching
   this engine may have been reconstructed from journaled JSON (Mission 2,
   unbuilt) under a relaxed or older schema, or produced by a future
   non-Claude `ReasoningProvider` — this engine's guarantee must not depend
   on another layer's diligence.
2. `trade_requires_minimum_history` — fails when `evidence.bar_count` is
   below what the snapshot's own declared indicator lengths require
   (`required_bars()`, mirroring `compute_indicators`'s availability
   minimums). Derived from the evidence's own parameters rather than a
   hardcoded constant, for the same reason the volatility thresholds entry
   above warns against unjustified magic numbers.
3. `trade_requires_known_trend` — fails only on `TrendDirection.UNKNOWN`.
   `SIDEWAYS` passes: blocking a range trade would be this engine
   substituting its own technical opinion for the AI's, which is exactly
   what re-deriving `analysis/`'s conclusions would do.
4. `trade_requires_momentum_and_volatility_evidence` — fails when `rsi` or
   `atr` is `None`. Checks availability only, never a value — no RSI
   threshold, no ATR band; a value-based rule would be an uncalibrated
   guess of exactly the kind this project has repeatedly refused to make
   without real historical data.

All five read only structured fields already computed by `analysis/` or
already validated on `ReasoningOutput`. None reads `ReasoningOutput.
rationale` (free-form text must never influence a deterministic rule) or
treats `confidence` as a threshold (a `confidence < X ⇒ WAIT` gate would be a
risk control, Risk Guardian's family of concerns, not this layer's — enforced
by two dedicated tests, `test_confidence_does_not_affect_the_decision` and
`test_rationale_does_not_affect_the_decision`, so a future change that wires
either in fails a test rather than passing silently).

**Rules considered and rejected:** a support/resistance-required rule (would
permanently block trades during a strong uptrend, when price is above every
prior swing high and no resistance level exists to find — a systematic false
block, not a safety property); a confidence floor (Risk Guardian's job, and
any specific number would be uncalibrated); a `bar_count`/indicator-null
cross-check (false positive: `pandas_ta.rsi` returns `NaN`, mapped to `None`,
on a constant-price series regardless of bar count — a legitimately flat
market, not inconsistent evidence).

**Fail-closed split:** `DecisionEngineError` (raised, never caught and
converted to a WAIT) is reserved for inputs the engine cannot evaluate at
all — wrong type, non-positive price, zero bars, a blank `analysis_id`. A
`ProposedDecision(action=WAIT, ...)` is returned, not raised, whenever both
inputs are individually valid but a rule fails — that is market uncertainty,
which must stay a fully auditable "I don't know," not an exception. This is
the same distinction `orion.reasoning.provider_base` already draws for
provider failures ("I could not reason" vs. "I reasoned, and the answer is
wait"), applied one layer up.

**Provenance without a persistence layer:** `analysis_id` and `now` are
required, caller-supplied parameters to `decide()`, not minted internally
(`uuid4()`, `datetime.now()`) — either would make two calls on identical
inputs produce unequal results, breaking the determinism requirement. No
orchestrator exists yet to mint them; callers and tests supply a literal
until one does. `EvidenceRef`/`ReasoningRef` reference their source objects
by a SHA-256 content hash (`orion.decision.schemas._content_hash`, over the
full source object including fields the ref itself doesn't display, e.g.
`ReasoningOutput.rationale`) rather than a new id field, since neither
`Evidence` nor `ReasoningOutput` carries one and no minting authority exists
yet. This is tamper-evidence and cycle-linkage, not a stable long-term
identifier — a schema change to either model changes the hash even though
nothing was tampered with.

**Known gaps, out of scope for this milestone:**
1. `ReasoningOutput` carries no reference back to the `Evidence` it was
   produced from, so a future orchestrator bug pairing mismatched
   evidence/reasoning is not detectable at this layer. Closing it means
   adding an `evidence_hash` field to the reasoning layer's schema — out of
   scope here since it touches `orion.reasoning`, not `orion.decision`.
2. `Evidence` is a plain (non-frozen) `BaseModel`; a caller mutating one
   after it's been hashed into an `EvidenceRef` would leave a stale hash.
   Making it frozen is a small future hardening in `orion.analysis`, not
   done here.

**Alternatives considered:** minting `analysis_id`/`now` inside the engine
(rejected — breaks determinism, see above). A single confidence-weighted
scoring rule instead of independent pass/fail checks (rejected — requirement
was explicit deterministic, individually-testable rules, and a blended score
would make it harder to say which specific check produced a WAIT). Building
risk/exposure checks into this layer per the brief's literal Mission 1D text
("risk/reward") (rejected — the founder's explicit requirement #14 and the
brief's own Mission 3 list both name minimum risk/reward as a Risk Guardian
control).

---

## 2026-08-23 — Evidence/reasoning linkage closed with a provider-stamped fingerprint

**Supersedes in part:** the entry directly above listed, as a known gap left
out of scope, that `ReasoningOutput` carried no reference back to the
`Evidence` it was produced from, so a mismatched evidence/reasoning pairing
was not detectable by the Decision Engine. That entry is left unedited as a
record of the reasoning at the time; this entry records closing that gap.

**Decision:** `orion.analysis.evidence.evidence_fingerprint(evidence)` is a
new function — SHA-256 over `evidence.model_dump(mode="json")` with sorted
keys and compact separators, the same canonicalization `orion.decision.
schemas` already used privately for its own content hashes, now promoted to a
named, importable, directly-tested function since two packages need to agree
on it exactly. `ReasoningOutput` (never `ReasoningVerdict` — the model-facing
schema is untouched) gains a required `evidence_fingerprint: str` field, a
64-character hex string, populated only by `ClaudeReasoningProvider.reason()`
computing it from the exact `Evidence` object it was called with. The model
is never asked to generate or report this value — it has no reliable way to
know it and every incentive to guess, the same reasoning that already kept
`model`/`prompt_version` out of the model-authored `ReasoningVerdict`.
`orion.decision.schemas.EvidenceRef.from_evidence` now calls
`evidence_fingerprint` too (replacing its own private hash for this one
field), so `EvidenceRef.content_hash` and `ReasoningOutput.evidence_fingerprint`
are byte-identical for a matching pair — verifying the linkage in
`orion.decision.engine.decide()` is a plain equality check, not a
reimplementation of the hash.

**How a mismatch is handled:** `decide()` checks
`evidence_fingerprint(evidence) != reasoning.evidence_fingerprint` immediately
after its type checks and before evaluating any rule, and raises a new
`EvidenceMismatchError(DecisionEngineError)` if they differ. This is a raise,
never a downgrade to a WAIT `ProposedDecision` — the existing fail-closed
split (see the entry above) draws the line at whether the engine can
*meaningfully evaluate* the inputs at all, and a mismatch means it cannot:
there is no basis to believe `reasoning` was produced from `evidence`, so
running a rule against them would be evaluating a coincidence, not a
proposal. Laundering it into a WAIT would make an orchestrator pairing bug
indistinguishable from ordinary market uncertainty in the audit trail, which
is exactly the failure mode `DecisionEngineError` exists to prevent.
`EvidenceMismatchError` is a distinct subclass (not just a distinct message)
so a caller can tell a pairing-integrity failure apart from a malformed input
if it ever needs to react differently, while still being caught by any
existing `except DecisionEngineError` handler. `ENGINE_VERSION` was bumped to
`2026-08-23.2` for this behavior change, per the same convention `PROMPT_VERSION`
already follows.

**Why:** requirement #5 (the engine is deterministic given identical Evidence
+ ReasoningOutput) was previously scoped to "these two objects, taken as
given." This closes the gap one level up: it was never checked that the
`ReasoningOutput` handed to `decide()` was actually about the `Evidence` also
handed to `decide()` — an orchestrator bug (not yet built, but anticipated)
pairing a fresh Evidence snapshot with a stale or mismatched ReasoningOutput
would previously have been silently evaluated as if they matched. A
provider-stamped, provider-verified fingerprint closes that without touching
persistence (still not introduced), without redesigning `Evidence` (only
`orion.analysis.evidence` gained one pure function), and without freezing any
model that wasn't already frozen.

**Known limitations:** the fingerprint proves *content* equality, not
*temporal* freshness — two `decide()` calls minutes apart against the same
unchanged snapshot still match, which is correct (nothing about the evidence
changed), but this mechanism cannot detect "this Evidence is stale," only
"this Evidence is not the one reasoning was produced from." Staleness remains
a Risk Guardian concern per the entry above. `Evidence` is still not frozen
(deliberately, per requirement #9): a caller that mutates an `Evidence` in
place after the fingerprint was already computed from it would produce a
divergence the fingerprint mechanism is specifically designed to catch, so
this is a property of the fix working, not a gap in it — but it is worth
noting that Mission 1D's other still-open item (freezing `Evidence`) would
make that class of bug impossible to construct in the first place, not just
detectable after the fact.

**Alternatives considered:** a new id field minted onto `Evidence` at
creation time (rejected — reintroduces the "who mints the id" problem
`analysis_id` was deliberately kept out of the engine to avoid, and
`Evidence` has no natural minting authority without a persistence layer).
Two independent hash implementations, one per package (rejected — a single
shared function is the only way to guarantee they can never silently drift
apart, which would either break every legitimate pairing or, worse, mask a
real one). Comparing `Evidence` objects directly with `==` instead of via a
fingerprint (rejected — `ReasoningOutput` is what crosses the process/
package boundary and gets journaled; carrying a hash is what makes the
proof portable to a future persisted record, where the original `Evidence`
Python object will no longer be in memory to compare against).

---

## 2026-08-23 — Mission 3 (Risk Guardian) completed: verdict-invariant
reachability bug fixed, guardian orchestration and import-hygiene tests added

**Context:** `orion.risk` (`policy.py`, `schemas.py`, `controls.py`,
`guardian.py`) already existed in full, matching the approved Mission 3
architecture exactly — the seven controls, the `review()` contract, the
fail-closed split between a raised `RiskGuardianError`/`EvidenceLinkageError`
and an auditable `VETOED`/`NO_TRADE_PROPOSED` verdict, and the
`stop_loss`/`take_profit` schema requirements on `ReasoningVerdict` (BUY/SELL
require both, ordered correctly; WAIT requires neither) were all already in
place from the prior session. What remained: 230 collected tests with 4
failing, and no `tests/risk/test_guardian.py` at all — the individual
controls were tested (`tests/risk/test_controls.py`), but `review()` itself,
the function that actually wires them together into a verdict, was not.

**Decision 1 — fix a real reachability bug in `RiskVerdict.
_check_verdict_invariants` (`orion.risk.schemas`):** the check for "the
guardian differs from the Decision Engine's proposal without a failed
control to justify it" was ordered *before* the more specific "the guardian
must never upgrade a WAIT proposal" check. Every case the specific check was
meant to catch (`decision_ref.action is WAIT`, `action` is not WAIT, no
control failed) also satisfies the generic check's condition (`action !=
decision_ref.action`, `not any_failed`), so the generic check always raised
first — the "never upgrade" check was unreachable dead code. Fixed by
swapping the order: the WAIT-upgrade check now runs first. Verified safe by
re-derivation, not just by the test suite going green: whenever `any_failed`
is `True`, the first invariant in the same validator (`any_failed and
action is not WAIT`) has already forced `action=WAIT`, so the reordered
WAIT-upgrade check's condition (`action is not WAIT`) can never be true in
that branch — reordering could not have made a previously-caught case
uncaught.

**Decision 2 — fix three incorrect test fixtures, not the production code
they were testing against:**
1. `tests/risk/conftest.py`'s `build_policy()` defaulted
   `blocked_volatility_levels` to `()`, silently disagreeing with
   `RiskPolicy`'s own fail-safe default of `(VolatilityLevel.UNKNOWN,)`.
   Every test that called `build_policy()` without overriding this argument
   was exercising a volatility policy no operator would actually run. Fixed
   the builder's default to match the real default.
2. `tests/risk/test_controls.py::test_evaluate_all_never_short_circuits`
   built evidence with `trend=UNKNOWN`, which the Decision Engine's own
   `trade_requires_known_trend` rule downgrades to WAIT before the proposal
   ever reaches the guardian — every trade-only control then reports
   `NOT_APPLICABLE`, not `FAILED`, trivializing the assertion this test
   exists to make. Fixed by keeping a known trend (so the proposal stays a
   real trade) and instead forcing two controls to fail on independent
   grounds (`kill_switch=ENGAGED` and `min_risk_reward=10.0` against the
   auto-filled RR=2.0 geometry `build_reasoning` supplies by default).
3. `tests/risk/test_schemas.py::test_no_trade_proposed_requires_wait_
   decision_ref` built its `RiskVerdict` fixture with a `_passed()` control
   outcome, which meant `any_failed=False` — under that condition the
   validator's generic "action differs from the Decision Engine's proposal"
   check fires before the `NO_TRADE_PROPOSED`-specific check this test
   targets ever runs (same reachability shape as Decision 1, but the correct
   fix here is the fixture, not the validator: this test's WAIT is supposed
   to be a *legitimately* narrowed one). Fixed by using `_failed()` instead,
   which is what actually makes `action=WAIT` valid while leaving the
   outcome/decision_ref mismatch as the sole violated invariant.

**Decision 3 — added `tests/risk/test_guardian.py` (33 tests) covering
`review()` end-to-end:** valid BUY/SELL/WAIT; WAIT can never be upgraded
even under a maximally permissive policy; kill switch engaged/disengaged;
evidence staleness and a future evidence timestamp; decision expiry and a
future decision timestamp; UNKNOWN volatility blocked by default, HIGH
allowed by default and blockable by explicit policy; coherent/incoherent
BUY and SELL geometry; a degenerate (zero-distance) stop veto that never
raises `ZeroDivisionError`; risk/reward exactly at the policy minimum
(accepted, `>=` is inclusive) and just below it (vetoed); a direct assertion
that the guardian's own computed ratio (not a self-reported one — no such
field exists on `ReasoningVerdict`) appears in `control_outcomes[...].
detail`; that `confidence`, `risk_level`, and `rationale` cannot move the
outcome (three tests, each holding a trade's geometry fixed while varying
only that one AI-authored field against a kill-switch-forced veto);
`EvidenceLinkageError` on an evidence/proposal mismatch; determinism
(identical inputs called twice produce an equal verdict); that
`decision_ref.content_hash`/`policy_ref.content_hash` match independently
recomputed `decision_fingerprint`/`canonical_hash` values; and six
`RiskGuardianError` raise-paths for malformed/reconstructed inputs (wrong
type, naive `now`, a blank `analysis_id`, a non-positive `last_price`, an
unrecognized timeframe) — the last few constructed via `model_copy` (which
skips validation) rather than direct construction, mirroring this package's
existing pattern for testing "reconstructed from a relaxed/older schema"
scenarios that a normal constructor call cannot produce.

**Decision 4 — added `tests/risk/test_import_hygiene.py`:** parses every
`orion.risk` source file's own AST for `import`/`from ... import`
statements (not a `sys.modules` runtime check, which would be contaminated
by whatever else the test process happens to have already imported) and
asserts none of them name a network, broker/provider, or persistence module
(`httpx`, `httpx2`, `requests`, `anthropic`, `sqlalchemy`, etc.). This is a
property of the package's source, not of this test run's import order —
`orion.risk.guardian.review` is documented as a pure function, and this test
is what keeps that claim mechanically checked rather than merely asserted in
a docstring.

**Verification:** `ruff check .`, `ruff format --check .`, and `mypy .`
(strict) all pass with zero findings; `pytest` collects 265 tests, all
passing (up from 230 collected / 226 passing at the start of this entry).
Two pre-existing, unrelated strict-mode findings surfaced and were fixed
while getting to a clean `mypy .`: a bare `tuple` (missing type argument)
on `build_policy`'s `blocked_volatility_levels` parameter in
`tests/risk/conftest.py`, and a stale `# type: ignore[call-arg]` in
`tests/risk/test_schemas.py::test_unexpected_field_is_rejected` left over
from before `_verdict`'s signature became `**overrides: object` (which
accepts any keyword, making the ignore comment inaccurate rather than
merely unnecessary).

**Known limitations, unchanged from the entries above and still correctly
out of scope for Mission 3:** no max daily/weekly loss, max drawdown, max
exposure, position size, or correlation/liquidity controls — all require
portfolio/account state that does not exist until Mission 4 (Paper
Trading). No calibrated volatility thresholds — `classify_volatility`'s
0.3%/0.8% cut points remain documented placeholders pending Mission 6. No
persistence layer — `RiskVerdict` is returned to the caller, not journaled;
Mission 2 (Decision Memory) is what will store it.
