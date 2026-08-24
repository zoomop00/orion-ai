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

---

## 2026-08-23 — Mission 4 (Paper Trading) architecture approved

**Context:** Mission 3 shipped with `orion.risk.guardian.review()` already
documenting its own next step: *"Mission 4 adds them by adding a required
second context object to `review()`"* — referring to the portfolio/account
state that max risk/trade, max daily/weekly loss, max drawdown, max
exposure, and position size all require and that Mission 3 explicitly did
not build (see the entry above, and `docs/DECISIONS.md`'s "No portfolio
state" precedent throughout the Mission 3 entries). This entry records the
architecture decided for that work before any of it is implemented. Nothing
in this entry is implemented yet — this is a decision record, not a status
report.

**Sequencing note:** the brief's phase order (`ORION_MASTER_ENGINEERING_
BRIEF_V2.md` §27) is Foundation → Brain → Memory → Risk Guardian → Paper
Trading. This repository built Risk Guardian (Mission 3) before Memory
(Mission 2); Mission 2 remains entirely unbuilt. B1 below is the decision
that lets Mission 4 proceed anyway without waiting on it.

**B1 — No dependency on Mission 2; persistence sits behind an interface.**
Paper Trading must operate fully in-memory for this milestone — no large
duplicate journal/database system gets built inside Mission 4 to work
around Mission 2 not existing yet. Account/position/fill storage is
accessed only through an abstract interface, mirroring the precedent
already established twice in this codebase (`orion.data.provider_base.
MarketDataProvider`, `orion.reasoning.provider_base.ReasoningProvider`): an
in-memory implementation ships now; Mission 2, whenever it lands, supplies
a durable implementation behind the same interface without the rest of
`orion.paper` or `orion.risk` changing. This is the same "swap the
implementation, not the pipeline" reasoning `docs/DECISIONS.md`'s market-
data-provider entry already used.

**B2 — Single open position at a time.** No scaling, averaging, pyramiding,
or multi-position portfolio model for v1. The single-symbol architecture is
unchanged (ORION trades exactly one instrument; see the Mission 3 entries'
repeated "correlation needs multiple simultaneously-tracked instruments"
reasoning, which this decision does not touch).

**B3 — Daily/weekly loss limits are percentage-based against an explicit
opening-equity baseline.** Daily loss is measured against the UTC day's
opening equity/balance; weekly loss against the UTC week's opening
equity/balance. The baseline is an explicit field on portfolio state — not
guessed, not reconstructed by re-deriving it from fills or any other
arbitrary source at check time. All bucketing is a pure function of
caller-supplied `now`, never wall-clock at call time, matching every other
timestamp-driven control already in `orion.risk` (`evidence_is_fresh`,
`decision_is_current`).

**B4 — Cash-covered only.** No leverage, margin, borrowing, or liquidation
model for v1. Position sizing (see requirement 6 below) is bounded entirely
by cash on hand.

**B5 — `PaperExecutionPolicy` stays separate from `RiskPolicy`.**
`RiskPolicy` answers *whether* a trade is permitted; `PaperExecutionPolicy`
controls *how* a permitted trade is simulated — spread, slippage, fees —
deterministically, with no randomness anywhere in the calculation. Kept
separate for the same reason `orion.decision`/`orion.risk` are separate
packages rather than one: each layer owns its own configuration, and
merging "is this allowed" config with "how is this simulated" config would
blur a distinction this codebase has drawn everywhere else.

**B6 — Last-price-only resolution for v1.** No intrabar high/low execution,
no latency realism, no advanced fill simulation — that is Mission 6's
explicit scope (`ORION_MASTER_ENGINEERING_BRIEF_V2.md` §12 lists slippage
and latency realism under Backtesting & Validation, not Paper Trading).

**B7 — Portfolio integrity is a raise; portfolio staleness is a veto.** A
portfolio/evidence linkage mismatch, or any input Risk Guardian cannot
meaningfully evaluate, raises a specific `RiskGuardianError` subclass — the
same fail-closed treatment `EvidenceLinkageError` already gives an
evidence/proposal mismatch one layer up, extended to the new portfolio
input. Ordinary portfolio *staleness* (old, but not mismatched or
malformed) is an auditable Risk Guardian control failure — VETOED, not
raised — matching the precedent `evidence_is_fresh`/`decision_is_current`
already set: a plausible-but-old input fails closed as a first-class
audited outcome, not an exception. Missing or stale portfolio state must
never be silently treated as "flat, no exposure" — that would let a broken
or absent snapshot pass every portfolio control by accident instead of
failing one.

**B8 — `risk_verdict_fingerprint()` closes the verdict provenance gap.** A
new function, using the same shared `orion.provenance.canonical_hash`
mechanism every other fingerprint in this codebase already uses
(`evidence_fingerprint`, `decision_fingerprint`), gives `RiskVerdict` a
content-hash of its own. Paper execution references the exact approved
`RiskVerdict` that authorized a simulated order by this fingerprint —
never an ad-hoc composite of `decision_ref`/`policy_ref`/`portfolio_ref`
fields reassembled at the call site, which would be a second, informal
identity mechanism competing with the one this codebase has deliberately
kept singular since the evidence/reasoning linkage entry above ("a single
shared function is the only way to guarantee they can never silently drift
apart").

**Additional requirements locked in for the implementation phase:**
1. `orion.risk` stays pure and side-effect-free — Paper Trading is a
   separate package that depends on `orion.risk`, never the reverse.
2. `PortfolioState` is a required parameter of `review()` — never
   `Optional`, never `None`-defaulted.
3. Paper Trading is the only layer permitted to mutate account state.
4. Paper Trading accepts only a `RiskVerdict` with `outcome is
   RiskOutcome.APPROVED` — never a VETOED or NO_TRADE_PROPOSED verdict.
5. `ExecutionMode` stays `PAPER`/`NONE` only. No `LIVE` member is added.
6. The AI does not propose position size; it is computed deterministically
   by the system from portfolio state and the proposal's own levels — the
   same "AI proposes, ORION computes and verifies" split already used for
   risk/reward.
7. Risk/reward remains independently computed by Risk Guardian, unchanged.
8. No correlation controls yet (needs multi-symbol tracking; out of scope).
9. No liquidity/order-book controls yet (needs data this provider tier does
   not have).
10. No realistic intrabar/latency simulation yet (Mission 6 scope; see B6).
11. No reflection-driven policy adaptation (Mission 5 scope — a paper
    outcome must never automatically rewrite policy or sizing).
12. The existing provenance chain (`evidence_fingerprint` →
    `decision_fingerprint` → `canonical_hash` → `RiskPolicyRef` → the new
    `risk_verdict_fingerprint`) is preserved and extended, never replaced.
13. Deterministic behavior and caller-supplied timestamps are preserved
    throughout — no `datetime.now()`, no `uuid4()`, no randomness anywhere
    in `orion.risk` or the new deterministic parts of `orion.paper`.
14. Fail-closed behavior is maintained: integrity failures raise, ordinary
    policy/limit breaches veto — never a silent pass-through.

**Alternatives considered:** waiting for Mission 2 before starting Mission
4 (rejected — B1; would block Paper Trading on a mission with no committed
timeline, when a clean storage interface lets both proceed independently).
A multi-position/scaling model for v1 (rejected — B2; unjustified
complexity for a single-symbol system with no demonstrated need yet). An
optional/`None`-defaulted `portfolio` parameter on `review()` for backward
compatibility (rejected — requirement 2; repeats exactly the unsafe-default
pattern this project has rejected everywhere else, e.g. the kill switch's
fail-safe `ENGAGED` default). Folding `PaperExecutionPolicy` into
`RiskPolicy` (rejected — B5). Randomized slippage "for realism" (rejected —
B5 and requirement 13; would break the determinism guarantee `review()`'s
own docstring asserts). Treating a missing/stale portfolio snapshot as a
flat account by default (rejected — B7; a false-negative risk that would
let a broken input pass every portfolio control silently).

**Known limitations, deferred by design, not oversight:** correlation
controls (needs multi-symbol tracking that does not exist), liquidity/
order-book controls (needs data this provider tier does not offer),
intrabar/latency-realistic fill simulation (Mission 6), reflection-driven
policy adaptation (Mission 5), any leverage/margin/liquidation model
(deferred past v1 by B4), and durable cross-run persistence of account
state (deferred to Mission 2 by B1, behind the interface boundary that
makes the deferral safe to make).

---

## 2026-08-23 — Mission 4 (Paper Trading) implemented per the approved
architecture

**Decision:** Implemented the architecture from the entry above exactly —
`orion.risk.portfolio` (new: `Position`, `PortfolioState`, `mark_to_market`,
`compute_position_size`, `initial_portfolio_state`), seven new Risk Guardian
controls, `review()`'s new required `portfolio` parameter, and a new
`orion.paper` package (`policy.py`, `schemas.py`, `store.py`,
`execution.py`) for deterministic simulated execution. No redesign — every
open question B1–B8 from the architecture entry is resolved exactly as
decided there.

**`orion.risk.portfolio` (new module, not `orion.paper` — see B1/layering):**
`Position` and `PortfolioState` are frozen Pydantic models with the same
validator discipline as every other schema in this codebase (`extra=
"forbid"`, cross-field invariants enforced on construction, defense-in-depth
duplicated from `orion.risk.controls.classify_trade_levels` rather than
imported from it, to avoid a `controls.py` ↔ `portfolio.py` import cycle
once `controls.py` needed to import `PortfolioState`). `equity_peak`/
`daily_opening_equity`/`weekly_opening_equity` are explicit fields, never
recomputed ad hoc (B3). `mark_to_market(state, *, now, last_price)` is the
one correct way to produce a fresh, self-consistent `PortfolioState`: it
re-marks equity, rolls `equity_peak` forward (monotonic high-water mark),
and rolls the daily/weekly opening-equity baselines forward across UTC day/
week boundaries — advancing in whole-period jumps, since ORION only marks
state at analysis-cycle granularity, not continuously. `compute_position_
size` is the single deterministic risk-based sizing formula every consumer
(the new Risk Guardian controls, and `orion.paper.execution`) shares, so
they can never disagree about what a proposal's size should be — the same
"one shared function" principle already used for provenance hashing.

**Seven new Risk Guardian controls, appended to `CONTROLS`** (existing
seven unchanged): `portfolio_state_is_fresh` (staleness, mirrors `evidence_
is_fresh`/`decision_is_current` exactly), `no_conflicting_open_position`
(B2 — single position at a time), `position_size_within_limits` (max
risk/trade: computes the deterministic size and fails closed if it is
non-positive or its notional exceeds available cash — cash-covered only,
B4), `exposure_within_limit` (a distinct bound from position sizing: caps
notional commitment as a fraction of equity regardless of stop distance),
`daily_loss_limit_not_breached`, `weekly_loss_limit_not_breached` (both
percentage-based against the explicit opening-equity baseline fields, B3),
and `max_drawdown_not_breached` (against `equity_peak`, distinct from the
daily/weekly baselines — drawdown never resets on a period boundary). Every
new control follows the identical NOT_APPLICABLE-on-WAIT / PASSED / FAILED
three-state pattern the existing seven already established, and none of
them special-cases the "all controls always run, never short-circuit"
guarantee — `evaluate_all`'s loop is unchanged in shape, only its argument
list grew.

**`RiskPolicy` gained six new fields**, all with conventional-starting-point
defaults documented as such (not empirically calibrated to XAUUSD, the same
transparency this file already applies to `min_risk_reward` and
`classify_volatility`'s thresholds): `max_risk_per_trade_fraction=0.01`,
`max_exposure_fraction=1.0`, `max_daily_loss_fraction=0.03`,
`max_weekly_loss_fraction=0.08`, `max_drawdown_fraction=0.15`,
`max_portfolio_staleness_bars=3.0` (shares `evidence_is_fresh`'s default
figure). `Settings`/`.env.example` gained the matching `ORION_MAX_*` fields
plus a new `PAPER_*` block for `PaperExecutionPolicy.from_settings`.

**`review()`'s new required `portfolio: PortfolioState` parameter** (B7,
requirement 2 — never `Optional`, never `None`-defaulted) added three new
integrity checks after the existing evidence-linkage check: `portfolio`
type/tzinfo checks (mirroring the existing proposal/evidence checks
exactly), and a new `PortfolioLinkageError(RiskGuardianError)` — raised
when `portfolio.symbol != evidence.symbol`, the Mission 4 analogue of
`EvidenceLinkageError`, checked by direct field equality (not a
fingerprint: `PortfolioState` carries no hash of `Evidence` to check
against, and symbol identity is the actual coherence question). Ordinary
portfolio *staleness* stayed a control failure, not a raise, per B7 — the
same fail-closed split `evidence_is_fresh`/`decision_is_current` already
established one layer down. `RiskVerdict` gained a required `portfolio_ref:
PortfolioStateRef` field (mirrors `EvidenceRef`/`RiskPolicyRef` exactly).
`GUARDIAN_VERSION` bumped to `2026-08-23.2` for the behavior change, per
the same convention `ENGINE_VERSION`/`PROMPT_VERSION` already follow.

**`risk_verdict_fingerprint(verdict)` (B8)** is a thin wrapper around the
shared `orion.provenance.canonical_hash`, added to `orion.risk.schemas`
alongside `RiskVerdict` — computed by the caller after `review()` returns,
never stored as a field on `RiskVerdict` itself (a self-referencing hash
field would need to exclude itself from its own input, which
`canonical_hash` does not special-case). `orion.paper.execution` uses it as
the sole identity `Fill`/`Position` reference back to the verdict that
authorized them — never the ad-hoc composite of `decision_ref`/`policy_ref`/
`portfolio_ref` fields B8 explicitly rejected.

**`orion.paper` (new package) — the only layer permitted to mutate account
state (requirement 3):** `policy.py` (`PaperExecutionPolicy`, separate from
`RiskPolicy` per B5 — spread/slippage/fee in basis points, zero randomness
anywhere in the calculation); `schemas.py` (`Order`, `Fill`,
`ClosedPosition`, `CloseReason`, `PaperExecutionError`); `store.py`
(`PortfolioStore` ABC + `InMemoryPortfolioStore`, mirroring `orion.data.
provider_base.MarketDataProvider`'s existing abstraction pattern exactly —
B1: Mission 2 can add a durable implementation behind this same interface
later without touching `orion.paper`'s execution logic or `orion.risk` at
all); `execution.py` (`execute()` for opening a position, `resolve_stop_
take_profit()` for last-price-only mechanical closes, B6).

**`execute()` accepts only an APPROVED verdict (requirement 4).** It also
independently re-checks `decision_fingerprint(proposal) == verdict.
decision_ref.content_hash` (the same "evaluating a coincidence" integrity
concern enforced at every other layer boundary in ORION) and that a
position is not already open, even though `orion.risk.controls.no_
conflicting_open_position` should already have vetoed that upstream — this
layer's guarantee must not depend on another layer's diligence, the
established defense-in-depth precedent. `quantity` is a required,
caller-supplied parameter, never recomputed internally from `RiskPolicy`
(which `orion.paper.execution` does not import at all): the caller is
expected to have used `orion.risk.portfolio.compute_position_size` — the
same function the Risk Guardian's own `position_size_within_limits` control
already used to decide the proposal was affordable in the first place
(requirement 6 — the AI never proposes a size).

**Cash accounting is direction-symmetric, not a real margin/borrow
model** (B4: cash-covered only). Opening any position (LONG or SHORT)
reduces cash by `notional + fee`; closing returns `quantity * entry_price +
realized_pnl` (net of the closing fee only — the opening fee already
reduced cash directly at open, so `ClosedPosition.realized_pnl` is not
double-counted). Because `Position.unrealized_pnl`'s sign-aware formula
already captures LONG/SHORT profit and loss correctly, this bookkeeping
reduces to a clean, direction-independent identity verified directly in
`tests/paper/test_execution.py`: `cash_after_close == cash_before_open -
fill.fee + closed.realized_pnl`. This is a deliberate simplification of
real short-selling mechanics (which involve genuine borrowing) — modeling
actual margin/borrow was explicitly out of scope per B4, and this identity
holds regardless of direction without inventing one.

**Last-price-only stop/take-profit resolution (B6).**
`resolve_stop_take_profit` is never gated by the Risk Guardian and takes no
`RiskVerdict` at all — a stop/take-profit level is a pre-committed exit the
AI already proposed and the Risk Guardian already approved when the
position was opened, so reaching it mechanically needs no new approval,
the same way a real broker's resting stop order does not re-ask
permission to fill. `ClosedPosition.close_reason` is `STOP_LOSS_HIT` or
`TAKE_PROFIT_HIT` only — v1 has no AI/Guardian-approved close path, so no
third reason exists yet.

**Test coverage:** `tests/risk/test_portfolio.py` (new, 33 tests:
`Position`/`PortfolioState` invariants, `mark_to_market`'s UTC day/week
rollover behavior and determinism, `compute_position_size`'s scaling and
zero-division safety), `tests/risk/test_controls.py` (extended with the
seven new controls' NOT_APPLICABLE/PASSED/FAILED/boundary cases),
`tests/risk/test_guardian.py` (extended with portfolio-integrity raises,
every new control's veto path, and provenance/determinism assertions
extended to cover `portfolio_ref`), `tests/paper/` (new package: `test_
policy.py`, `test_schemas.py`, `test_store.py`, `test_execution.py`,
`test_import_hygiene.py` — the last mirroring `tests/risk/test_import_
hygiene.py`'s AST-based static-import check). `tests/paper/test_execution.
py` includes the full BUY → APPROVED → PAPER FILL → updated `PortfolioState`
→ next `review()` feedback-loop test the architecture's new state
dependency introduced (`test_full_buy_approve_fill_feedback_loop_blocks_a_
second_trade`), plus its mirror proving a closed position frees the account
for a new trade. `tests/risk/conftest.py`'s `build_proposal` needed one
fixture change beyond additive builders: its default BUY/SELL trade-level
auto-fill widened from the pre-existing 2385.0/2430.0 (~0.6% stop distance)
to a 2%-of-price offset, because the narrower geometry is mathematically
unaffordable under `RiskPolicy`'s default 1% risk-per-trade with no
leverage (notional/equity is fixed at `risk_fraction * entry/stop_distance`
regardless of account size, and 0.6% stop distance against 1% risk implies
1.6x leverage) — every test that constructed a default-geometry BUY/SELL
expecting APPROVED needed this fix to keep passing under the new
cash-covered sizing controls; no test asserting a *specific* level value
(all pass explicit `stop_loss`/`take_profit`) was affected.

**Verification:** `ruff check .`, `ruff format --check .`, and `mypy .`
(strict) all pass with zero findings. `pytest` collects 389 tests, all
passing (up from 268 at the start of this entry: 232 in `tests/risk`+
`tests/paper` combined are new or touched by this milestone). Manually
verified beyond the test suite: no network/broker/live-execution imports in
`orion.risk` or `orion.paper` (the two AST-based hygiene tests); paper
execution structurally cannot consume a VETOED or NO_TRADE_PROPOSED verdict
(`execute()` raises `PaperExecutionError` on anything but APPROVED);
identical inputs produce identical results end-to-end (`review()`,
`mark_to_market`, and `execute()` each have a dedicated determinism test);
cash conservation and BUY/SELL P&L sign correctness hold across a full
open/close round trip in both directions; position sizing is bounded by
the configured risk fraction and independently re-verified for cash
feasibility; portfolio limits (daily/weekly loss, drawdown, exposure,
conflicting position) each correctly veto; stale portfolio state fails
closed as an auditable VETOED verdict, never silently read as flat;
provenance hashes (`evidence_fingerprint`, `decision_fingerprint`,
`canonical_hash`, `risk_verdict_fingerprint`) all independently recompute
to the values stored in `RiskVerdict`/`Fill`/`Position`.

**Known limitations, unchanged from the architecture entry above:**
correlation and liquidity controls remain out of scope (need multi-symbol
tracking and order-book depth data this system does not have);
intrabar/latency-realistic fill simulation remains Mission 6's scope;
reflection-driven policy adaptation remains Mission 5's scope; no
leverage/margin/liquidation model exists past v1's cash-covered design;
account state does not survive a process restart (`InMemoryPortfolioStore`
only) until Mission 2 supplies a durable implementation of the same
`PortfolioStore` interface. One new, narrow limitation surfaced during
implementation and is recorded here rather than silently left undocumented:
`orion.paper.execution.execute`'s defensive check for a proposal with no
`stop_loss`/`take_profit` is provably unreachable through any construction
path that also satisfies the `decision_fingerprint` linkage check (an
APPROVED verdict already implies `trade_levels_are_coherent_with_price`
passed, which requires both levels), so it has no dedicated test — the
same category of "restated defense-in-depth for a scenario another layer's
diligence already prevents" this codebase has accepted elsewhere (e.g.
`proposal_was_not_upgraded`'s docstring), not a gap in coverage.

---

## 2026-08-23 — Fix Mission 4 portfolio concurrency (H-NEW1 replay
protection, H-ABA1 monotonic revision) — backfilled entry

**This entry was written retroactively, during Mission 2 planning, after
noticing commit `2b3189d` ("Fix Mission 4 portfolio concurrency") shipped
with no corresponding decision log entry.** The change itself was reviewed
and committed correctly at the time; only the log entry was missed. Recorded
now, out of chronological order, rather than edited into the Mission 4
implementation entry above, per this file's own append-only rule.

**H-NEW1 (found in a skeptical post-implementation review of Mission 4):**
the original `PortfolioStore` interface had no replay/idempotency
protection at all — a caller retrying `execute()` + a hypothetical `save()`
with a stale-but-still-plausible `PortfolioState` could double-apply a
fill, silently corrupting the account. `orion.paper.execution.execute`
deliberately stays a pure function with no store dependency (an
architectural requirement, not an oversight), so the fix had to live at the
store boundary instead.

**First fix attempt used `orion.risk.portfolio.portfolio_state_fingerprint`
(a pure content hash, newly added) as the compare-and-swap token for
`PortfolioStore.save()`.** A second, explicitly-requested skeptical review
(H-ABA1) found this vulnerable to the classic ABA problem: if account
content genuinely passes through A → B → C where C happens to be
byte-identical to A (a legitimate round trip, e.g. a position opened and
closed such that cash returns to a prior value), a caller still holding
A's fingerprint from before B ever happened could successfully write
against C, because C's fingerprint equals A's.

**Final fix: `PortfolioStore` gained a store-minted, monotonically
increasing `revision: int`, never a content fingerprint, as the CAS token.**
`load()`/`save()`/`initialize()` all now return a new `PortfolioRecord`
(`state` + `revision`) atomically in one call, so a caller never needs a
second round trip to learn the revision to present next.
`PortfolioStoreConcurrencyError(PortfolioStoreError)` is raised, and the
stored state/revision left completely unchanged, whenever
`expected_revision` does not match the store's actual current revision —
including a revision that is stale only because content coincidentally
repeated (the exact case a fingerprint-based CAS gets wrong).
`portfolio_state_fingerprint` itself was kept, unchanged, as a pure
content-identity/provenance hash (used by `PortfolioStateRef` and
elsewhere) — concurrency identity and content identity are deliberately
two different mechanisms answering two different questions, and conflating
them into one hash is exactly what let the ABA gap in.

`orion.paper.execution.execute` gained one additional integrity check,
found necessary during the same review: an explicit comparison of
`verdict.action`/`verdict.direction` against
`verdict.decision_ref.action`/`.direction`, rather than relying on
`Position`'s/`Fill`'s own geometry/NONE-rejection validators to incidentally
catch a `model_copy`-tampered verdict as a side effect — the same
defense-in-depth discipline already applied to
`proposal_was_not_upgraded`/`classify_trade_levels`.

**Test coverage added:** `tests/paper/test_store.py` extended with the
full CAS/ABA suite (stale-write rejection, the constructed ABA scenario,
sequential-revision chaining, deterministic rejection messages);
`tests/paper/test_replay_protection.py` (new — end-to-end `execute()` +
`InMemoryPortfolioStore.save()`, proving a retried/replayed execution never
lands a second fill regardless of store content coincidentally repeating).

**Verification at the time:** `ruff check .`, `ruff format --check .`,
`mypy .` (strict) all clean; `pytest` at 389 passing (unchanged count from
the Mission 4 implementation entry above — this was a fix to already-counted
tests plus new ones, verified again independently before the Mission 2 work
below began).

---

## 2026-08-24 — Mission 2 (Decision Memory) architecture approved

**Decision:** Implement Mission 2 as a new `orion.memory` package providing
two things behind two existing/new interfaces, and nothing else: (1) a
durable `SqlitePortfolioStore(PortfolioStore)` — the durable implementation
of the *existing* `orion.paper.store.PortfolioStore` interface Mission 4
left as an open seam (B1) — and (2) a new, append-only `DecisionJournal`
interface recording every `Evidence` / `ReasoningOutput` / `ProposedDecision`
/ `RiskVerdict` / `Fill` / `ClosedPosition` ORION ever produces, with a
SQLite-backed implementation. Both back onto one SQLite database via
SQLAlchemy Core, per the storage technology already decided in this file's
very first Mission 0 entry ("Decision memory: SQLite").

**Why SQLite + SQLAlchemy Core, not the ORM:** every record is already a
fully-specified Pydantic domain model; mapping each one a second time onto
an ORM class would create two parallel schemas that can silently drift.
Core lets every table store the record's own `model_dump_json()` as the
source of truth, plus a handful of denormalized, indexed columns for what
is actually queried (fingerprint, `analysis_id`, `symbol`, the record's own
domain timestamp) — a new field on a domain model needs no migration unless
it also needs to be queryable.

**M2-Q4 — content fingerprint as primary key, approved.** Every journal
table's primary key is the record's own content fingerprint
(`evidence_fingerprint`/`canonical_hash`/`decision_fingerprint`/
`risk_verdict_fingerprint` — the exact functions already used everywhere
else in ORION), computed internally by `SqliteDecisionJournal`, never
accepted as a parameter (a structural anti-forgery property: there is no
argument through which a caller could supply a fingerprint disagreeing with
an object's actual content). This is deliberately one mechanism answering
two questions at once: it is both the provenance-linkage mechanism and the
idempotency/replay-protection mechanism — a duplicate write of
byte-identical content is a database-level no-op.

**M2-Q3 — `PortfolioState` history as append-only INSERT with
`revision` as the table's own primary key, not `UPDATE ... WHERE revision =
?`, approved.** This revises (for the durable implementation only) the
`UPDATE`-based sketch in `orion.paper.store`'s own module docstring,
written before this durable implementation actually existed. Every
revision this store has ever issued is kept as a permanent row — no
account-state history is ever destructively overwritten, matching this
file's immutability convention even for the one entity that is
conceptually "current value, not append-only fact" — at no extra cost, and
it is what makes the CAS safe under real concurrent writers: two writers
racing to insert the same revision number are resolved by the database's
own `PRIMARY KEY`/`UNIQUE` constraint at the moment of insert, not by
whatever isolation level happens to govern the preceding read. `PortfolioStore`'s
public interface (`load`/`save`/`initialize` returning `PortfolioRecord`)
is completely unchanged; only what backs it durably.

**M2-Q6 — `recorded_at` as a required, caller-supplied parameter on every
`DecisionJournal.record_*` method, approved** ("when did Memory learn this,"
distinct from the record's own domain timestamp) — matching the
determinism discipline every other layer already follows (`decide`,
`review`, `execute` all take `now` as a required parameter, never read a
clock themselves). `PortfolioStore.save()`/`initialize()`'s own signature
was deliberately *not* changed to add an equivalent parameter — that
interface is Mission 4's, preserved exactly per this approval's own scope
limit, and `PortfolioState.as_of` (already required, already
caller-supplied) already serves the equivalent purpose for that table.

**M2-Q7 — "historical search" scoped to mechanical filter/pagination
only, approved.** `DecisionJournal.list_verdicts_by_symbol` supports a
`start`/`end`/`limit`-bounded range query; nothing semantic or
similarity-based. "Semantic validated lessons," "performance memory," and
"similar-setup retrieval" from the brief's Mission 2 list remain
out of scope — they require either decision-quality judgment (Mission 5
Reflection's job, per the brief's own §11) or embedding/similarity search
(Mission 5/7 territory), neither of which this milestone implements.
"Short-term session context" is also out of scope: no session/UI concept
exists anywhere in this codebase to attach it to.

**M2-Q9 — no orchestrator/`pipeline.py`, approved.** `orion.memory` ships
as a storage layer with no caller wiring it into a live sequence of
`record_*` calls, the same situation Mission 4 shipped in with zero
production callers of `PortfolioStore`. Building an orchestrator is its own
set of decisions (retry policy, scheduling) deliberately not made as a side
effect of this storage-layer task.

**H (transaction/atomicity boundary):** one `record_*`/`save()`/
`initialize()` call is one atomic SQLite transaction; there is no
cross-entity transaction spanning an entire analysis cycle, because no
orchestrator exists to define one, and because it is the *correct* answer
regardless — content-addressed writes are individually idempotent and
safely retryable, so a journal with an `Evidence` row and nothing else
downstream of it yet (e.g. after a crash) is a legitimate, honest,
resumable state, not corruption.

**Compatibility with Mission 3/4 — verified, not merely intended:** zero
changes to any `orion.risk`/`orion.decision`/`orion.reasoning`/`orion.
analysis`/`orion.paper` source file (confirmed via `git diff --stat`
against this commit — only new files under `src/orion/memory/` and
`tests/memory/`). `SqlitePortfolioStore` implements the *existing*
`PortfolioStore` ABC unchanged; `InMemoryPortfolioStore` is untouched and
still backs Mission 4's own fast unit tests. Two new AST-based
import-hygiene tests (`tests/memory/test_import_hygiene.py`) mechanically
prove `orion.risk` and `orion.paper` do not import `orion.memory` — the
existing `tests/risk/test_import_hygiene.py`/`tests/paper/test_import_
hygiene.py` already proved neither imports `sqlalchemy` directly; this
closes the complementary gap (an indirect persistence dependency via
`orion.memory` without importing `sqlalchemy` directly).

**Fail-closed database-layer errors:** `DecisionJournalError(RuntimeError)`
(own top-level hierarchy, never inheriting `PortfolioStoreError` or any
other layer's) and its `DecisionJournalIntegrityError` subclass (a stored
payload's recomputed fingerprint no longer matches its own primary key —
corruption, tampering, or, in principle, a hash collision on write) mirror
`orion.paper.store.PortfolioStoreError`'s "I could not persist" vs. "I
persisted, and here is what happened" distinction. Both `SqliteDecisionJournal`
and `SqlitePortfolioStore` wrap every unexpected `sqlalchemy.exc.
SQLAlchemyError` (connection loss, disk I/O failure) in their own error
type — proven with a fake-engine test that forces a real `OperationalError`,
not merely inspection of the `except` clause — matching the same
provider-boundary wrapping discipline `orion.reasoning.claude_provider`
already applies to every Anthropic SDK failure.

**Concurrency:** every connection gets `PRAGMA journal_mode=WAL` and
`PRAGMA busy_timeout=5000` (`orion.memory.engine.create_memory_engine`),
verified live (not merely configured) by reading both pragmas back from a
real file-backed connection. Proven under genuine multi-threaded,
multi-connection contention (a real temp-file database — deliberately not
the `:memory:` + `StaticPool` test fixture, which pins every checkout to a
single shared connection and so cannot exercise real cross-connection
locking): ten threads racing to `save()` against the same `expected_revision`
resolve to exactly one acceptance and nine `PortfolioStoreConcurrencyError`
rejections; a five-thread reload-and-retry-on-conflict loop advances the
store by exactly the intended number of revisions with none skipped or
issued twice. The same classic-ABA-attack scenario from the H-ABA1 entry
above was re-run against `SqlitePortfolioStore` directly and rejected
identically.

**Restart persistence — proven, not assumed:** a dedicated test
initializes and saves through one `Engine` pointed at a real temp file,
disposes it (simulating a process exit), opens a second, independent
`Engine` against the same file path (simulating a restart), and confirms
`load()` returns the exact prior state and revision; a second test confirms
a stale `expected_revision` is still correctly rejected after that restart.

**Files added:** `src/orion/memory/{schema.py, engine.py, journal.py,
sqlite_journal.py, portfolio_store.py}`; `tests/memory/{conftest.py,
test_journal.py, test_portfolio_store.py, test_import_hygiene.py}`. No
existing file modified except this log and (additively) `docs/DECISIONS.md`
itself.

**Verification:** `ruff check .`, `ruff format --check .`, and `mypy .`
(strict) all pass with zero findings. `pytest` collects 466 tests, all
passing (up from 460 immediately after the new package's own 36 tests were
first added, then 466 after adding the fail-closed-wrapping proof tests
described above — 42 tests total under `tests/memory/`). Adversarial checks
performed beyond the test suite, per the standing skeptical-review
requirement for this codebase: duplicate-write idempotency (identical
content, byte-for-byte, regardless of `recorded_at`, produces exactly one
row); simulated fingerprint collision (a row's stored payload manually
overwritten at the database level, then the true content re-recorded under
its real fingerprint) correctly raises `DecisionJournalIntegrityError`
rather than silently keeping either version; simulated on-disk corruption
(a stored payload edited to a value that still parses but no longer hashes
to its own primary key) is caught on read, not just write; every rejected
CAS write (stale revision, ABA, concurrent race) leaves the store's prior
state and revision completely unchanged, verified by explicit assertion
after each rejection, not merely by the absence of an exception elsewhere.

**Known limitations, deferred by design, not oversight:** semantic/
similarity search, aggregate performance statistics, and reflection-derived
"lessons" remain entirely out of scope (Mission 5, per M2-Q7 above); no
orchestrator exists to actually call `DecisionJournal.record_*` in
production sequence yet (M2-Q9); `DecisionJournal` covers `Evidence`/
`ReasoningOutput`/`ProposedDecision`/`RiskVerdict`/`Fill`/`ClosedPosition`
only — it does not itself journal `PortfolioState` history (that is
`SqlitePortfolioStore`'s append-only `portfolio_state_revision` table, a
deliberately separate contract, per the architecture entry above); no
schema-migration tooling exists yet (not needed until a genuine breaking
schema change is required — the JSON-payload-plus-indexed-columns design
absorbs additive domain-model changes without one).

---

## 2026-08-24 — `DecisionJournal` gains Fill/ClosedPosition read methods
(Mission 5 prerequisite); two architectural findings documented

**Context:** a fresh end-to-end Missions 2–4 integration audit (Evidence →
ReasoningOutput → ProposedDecision → RiskVerdict → Paper Execution →
PortfolioState → Decision Memory) found no HIGH/CRITICAL issues, but
surfaced one concrete MEDIUM prerequisite blocking Mission 5: `DecisionJournal`
could `record_fill`/`record_closed_position` but had no corresponding
`get_fill`/`get_closed_position`/`list_fills_by_*`/`list_closed_positions_by_*`
— meaning Mission 5's core input (realized P&L, close reason, fill price/fee
— the "actual outcome" half of "Prediction → Actual outcome → Difference →
Decision-quality analysis → Lesson", brief §11) had no read path through the
documented interface at all.

**Decision: add six new read-only methods to `DecisionJournal`, additively,
mirroring the existing `RiskVerdict` query methods exactly.** `get_fill`/
`get_closed_position` mirror `get_evidence`/`get_verdict` (fingerprint in,
object-or-`None` out, same `_deserialize`-then-recompute-fingerprint
integrity check). `list_fills_by_verdict_fingerprint`/`list_closed_positions_
by_verdict_fingerprint` mirror `list_verdicts_by_analysis_id` (a "by the
fingerprint that links back to a decision" query, ordered by `recorded_at`
ascending) — a tuple, not `Fill | None`/`ClosedPosition | None`, because
Mission 4 v1 opening at most one position per verdict does not make more
than one Fill for that verdict impossible (a replayed/retried `execute()`
call at a different `now` produces a second, distinct fingerprint; nothing
in this interface should assume otherwise). `list_fills_by_symbol`/
`list_closed_positions_by_symbol` mirror `list_verdicts_by_symbol` exactly
(`symbol`, optional `start`/`end` against `filled_at`/`closed_at`, optional
`limit`, ascending order). No new hashing mechanism: `Fill`/`ClosedPosition`
have no domain-named fingerprint wrapper (unlike `evidence_fingerprint`/
`decision_fingerprint`/`risk_verdict_fingerprint`), so these methods use
`orion.provenance.canonical_hash` directly, exactly as `record_fill`/
`record_closed_position` already did on write.

**Schema change: `fill.symbol` and `closed_position.symbol` gained an index**
(previously present but unindexed, unlike `risk_verdict.symbol`) — the one
addition `list_fills_by_symbol`/`list_closed_positions_by_symbol` actually
need; no other schema change. `_verdicts_from_rows` was generalized into a
private `_rows_to_models` helper (parameterized by model class and
fingerprint function) so the four new `list_*` methods reuse the same
integrity-checked reconstruction loop instead of duplicating it — an
internal refactor, not an interface or behavior change.

**All existing guarantees preserved, verified, not merely asserted:** no
update/delete method exists on `DecisionJournal` (`__abstractmethods__`
introspected directly in a new regression test — the six new method names
are confirmed present and none contain update/delete/remove/mutate/set_);
content-addressed idempotent writes, fingerprint integrity checks, and
`DecisionJournalIntegrityError` on corrupted payloads all re-verified for
Fill/ClosedPosition specifically (invalid JSON, schema-invalid-but-parseable
JSON, and hash-mismatch-after-tampering, mirroring the exact three-case
matrix already proven for Evidence/ReasoningOutput/ProposedDecision/
RiskVerdict); deterministic ascending ordering and pagination boundaries
(`limit=0`/`limit` exceeding actual count) verified; restart persistence
verified (a `Fill`/`ClosedPosition` written through one `Engine`, on a real
temp-file database, correctly reconstructs — including through every new
`list_*` method — from a second, independent `Engine` opened against the
same file). Zero changes to `orion.risk`/`orion.paper`/any existing
fingerprint function; `orion.paper.execution`/`orion.paper.store` contracts
untouched.

**Finding A — journal/account transaction boundary (documented, not
fixed in this entry; explicitly out of scope for this task).**
`SqliteDecisionJournal` and `SqlitePortfolioStore` are independently
committed transactions — each `record_*`/`save()` call is its own
`engine.begin()` block, even when both are backed by the same database
file. A crash between "account state saved" and "fill journaled" (in
either order) is architecturally possible and was previously undocumented.
**For the future Mission 6 orchestrator: treat `PortfolioStore` as
authoritative and persist it first; call `journal.record_fill()`/
`record_closed_position()` second.** If the second step fails or the
process crashes before it runs, retrying the journal write alone is safe —
it is content-addressed and idempotent, so replaying it after a restart
never creates a duplicate or requires re-executing the trade. This entry
does not attempt to create a cross-package transaction spanning both
stores; that would either violate `PortfolioStore`'s Mission 4 interface-
preservation requirement or require the two packages to share a connection
in a way neither currently does, and was explicitly out of scope for this
task.

**Finding B — Fill/ClosedPosition authenticity is a writer-trust boundary,
not a proof (documented, not fixed in this entry; explicitly out of scope
for this task).** `DecisionJournal` verifies that a stored `Fill`/
`ClosedPosition` has not been altered since it was written (content
integrity) and that its `risk_verdict_fingerprint`/`opened_by_verdict_
fingerprint` refers to a real fingerprint string — but it cannot prove the
object was actually computed by `orion.paper.execution.execute`/
`resolve_stop_take_profit` rather than hand-constructed with a legitimate
verdict's fingerprint attached to fabricated numbers (confirmed empirically
during the audit: a hand-built `Fill` with an absurd `fill_price` and a
real verdict's fingerprint was accepted by `record_fill` without
complaint). This is consistent with Mission 2's already-documented
"no cross-record ordering/authenticity enforcement by design" scope (no
orchestrator exists to be the sole legitimate caller yet) — restated here
explicitly because Mission 5 will read these records as ground truth for
"actual outcome," and should know the guarantee is provenance and
integrity, not computational proof. **This entry does not redesign Fill
provenance or introduce a second execution-attestation system** — the
correct long-term mitigation (a future orchestrator being the only
production caller of `record_fill`/`record_closed_position`, always
passing `execute()`'s own return value directly) is a Mission 6
orchestrator-design concern, not a Decision Memory schema concern.

**Files added:** `tests/memory/test_journal_fills_and_closed_positions.py`
(29 new tests). **Files modified:** `src/orion/memory/journal.py` (6 new
abstract methods), `src/orion/memory/sqlite_journal.py` (6 new
implementations + the `_rows_to_models` refactor), `src/orion/memory/
schema.py` (two new indexes), `docs/DECISIONS.md` (this entry).

**Verification:** `pytest` collects 511 tests, all passing (up from 482).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass
with zero findings.

---

## 2026-08-24 — Mission 5 (Reflection) architecture approved and implemented

**Decision: a new, pure, read-only package — `src/orion/reflection/` —
implementing exactly the master brief's four-line Mission 5 spec ("After
each paper trade: Prediction → Actual outcome → Difference →
Decision-quality analysis → Lesson. Distinguish a bad outcome from a bad
decision. Do not change production strategy because of one trade.") and
nothing beyond it.** No orchestrator, no persistence, no LLM call, no
semantic/vector similarity, no autonomous adjustment of `RiskPolicy` or
reasoning prompts. `orion.risk`/`orion.paper`/`orion.memory` are unchanged.

**Investigation finding, confirmed empirically before writing any code:**
tracing the actual read chain live against a real journal showed Mission 5's
core computation needs only three `DecisionJournal` reads —
`get_closed_position` → `get_verdict` (via `opened_by_verdict_fingerprint`)
→ `get_decision` (via `verdict.decision_ref.content_hash`) — not the full
six-entity chain. `ProposedDecision.reasoning_ref`/`.evidence_ref` already
carry everything needed for "prediction" (action, direction, confidence,
risk_level, stop_loss, take_profit, and the evidence reference price),
confirmed live (`hasattr(reasoning_ref, "rationale")` is `False`, confirming
rationale stays structurally unreachable, exactly as designed one layer up).
**No existing Mission 2–4 contract required extension** — the read API
shipped in the immediately preceding entry was already sufficient.

**The single load-bearing design decision: `TradeReflection` exposes
objective facts only — no quality score, no good/bad verdict, no field a
caller could wire up to automatically change strategy.** This is the
concrete mechanism that satisfies "distinguish a bad outcome from a bad
decision" and "do not change production strategy because of one trade"
simultaneously: `approval_margin` (predicted risk/reward minus the policy
minimum that was actually in force) and `exit_matched_predicted_take_
profit`/`exit_matched_predicted_stop_loss` let a human see whether a losing
trade had a thin or comfortable margin, and which predicted level actually
fired — the judgment is left entirely to whoever reads a `TradeReflection`,
never made by this module. Computing that judgment here would make
Reflection a second risk authority sitting downstream of `orion.risk.
guardian.review`, with its own thresholds that could silently diverge from
the one that actually gates execution — the one outcome this design
exists to prevent.

**`reflect_on_closed_position(journal, closed_position_fingerprint, *,
now)` — the whole of v1.** Pure function, matching `orion.decision.engine.
decide`/`orion.risk.guardian.review`/`orion.paper.execution.execute`'s
identical discipline: `now` is required and caller-supplied, stamped onto
the result as `reflected_at`; no `datetime.now()`/`uuid4()`/`random`/
`time.time()` anywhere. Typed against `DecisionJournal` (the abstract
interface, never `SqliteDecisionJournal` or any other concrete class) and
never against `PortfolioStore` in any form — there is no path through this
function's own signature for it to touch current account state, and
`DecisionJournal` itself declares no update/delete method, so even a bug
in this module has no method available to call that could mutate history.
The predicted risk/reward ratio reuses `orion.risk.controls.
classify_trade_levels` (the exact coherence check `risk_reward_meets_
minimum` performs first) plus that control's own inline formula — there is
no separately exported ratio-computing function, so this is the faithful
reuse, not a second implementation. Every fingerprint on `TradeReflection`
is copied directly from fingerprint fields already present on the
journaled records; no new hashing mechanism was introduced.

**Fail-closed on an incomplete chain.** Mission 2 deliberately does not
enforce cross-record write ordering (a documented, prior decision), so a
`ClosedPosition` can exist in the journal without its `RiskVerdict` (or
that verdict's `ProposedDecision`) ever having been journaled. `reflect_on_
closed_position` raises `ReflectionError` (its own top-level `RuntimeError`
subclass — never inheriting `DecisionJournalError`, matching the
established "every layer owns its own error hierarchy" rule) on any
missing link, never a partial or best-guess result.
`DecisionJournalIntegrityError` from a corrupted journal record is never
caught here — it propagates unchanged, verified directly (a corrupted
`ClosedPosition` payload, and a hash-mismatched one, both surface as
`DecisionJournalIntegrityError` through `reflect_on_closed_position`
unmodified).

**The opening `Fill` is optional enrichment, not a required link.** Looked
up via `list_fills_by_verdict_fingerprint` and matched to the
`ClosedPosition` by `fill.filled_at == closed.opened_at` — the exact
instant the position was opened. Zero matches leaves `opening_fee` `None`;
more than one match (e.g. a corrupted duplicate write, or in principle a
replay that was mistakenly journaled) is a genuine ambiguity this function
refuses to resolve by guessing, and raises `ReflectionError` instead. A
second `Fill` for the *same* verdict at a *different* `filled_at` is
correctly ignored, not treated as ambiguous — verified directly.

**`TradeReflection`** — frozen, `extra="forbid"`, matching every schema in
this codebase — carries: provenance (`closed_position_fingerprint`,
`opened_by_verdict_fingerprint`, `decision_fingerprint`); prediction
(`predicted_action`/`direction`/`confidence`/`risk_level`/`stop_loss`/
`take_profit`/`risk_reward`); policy context (`policy_min_risk_reward`,
`approval_margin`); actual outcome (`close_reason`, `entry_price`,
`exit_price`, `realized_pnl`, `closing_fee`, `opening_fee`); and the
difference (`exit_matched_predicted_take_profit`/`stop_loss`); plus
`reflected_at`. A light cross-field validator confirms the two
`exit_matched_*` booleans agree with `close_reason` — restated
defense-in-depth against a hand-constructed `TradeReflection` with
inconsistent fields, the same category of check this codebase already
applies pervasively (`RuleOutcome`, `ControlOutcome`).

**Persistence explicitly deferred, not designed around.** `TradeReflection`
is a pure function of already-immutable, already-durable journal data, so
persisting it would be an optimization (avoid recomputation), never a
necessity — nothing is lost by not persisting it, since it can always be
recomputed on demand. Designing a schema for it now, with no real consumer
yet (no orchestrator exists for Missions 2–4 either), would repeat exactly
the "building ahead of proven need" this project has deliberately avoided
at every prior mission boundary. Revisit once Mission 6 or a human-review
workflow actually needs durable storage for it.

**Files added:** `src/orion/reflection/{__init__.py, schemas.py,
analysis.py}`, `tests/reflection/{conftest.py, test_analysis.py}` (17
tests). No existing source file modified.

**Verification:** `pytest` collects 528 tests, all passing (up from 511).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass
with zero findings.

---

## 2026-08-24 — Mission 6 (Backtesting & Validation) architecture approved and implemented

**Decision: a new package — `src/orion/backtest/` — wiring the existing
Evidence → Reasoning → Decision → Risk Guardian → Paper Execution chain
into one deterministic, single-symbol/single-position loop over historical
bars, with zero modification to any Mission 2–5 contract.** Five open
questions from the read-only architecture investigation were resolved and
locked before implementation began:

- **M6-Q1 (historical data):** a new, small `HistoricalBarSource` ABC
  (`src/orion/backtest/data.py`), not an extension of `orion.data.
  provider_base.MarketDataProvider` — that interface's `fetch_ohlcv(...,
  output_size)` is a "most recent N bars" live-fetch contract, structurally
  different from "every bar between two dates." Two v1 implementations
  only: `InMemoryHistoricalBarSource` and `CsvHistoricalBarSource` (the
  latter delegates to the former after eager, fail-closed CSV parsing).
  No Twelve Data range integration, no new network dependency. Neither
  implementation ever reorders, deduplicates, or repairs a dataset —
  `orion.data.validation.validate_snapshot` (called once, up front, by the
  engine) is what classifies and fails closed on bad data.
- **M6-Q2 (intrabar ambiguity):** a bar whose `[low, high]` range reaches
  both the stop-loss and the take-profit resolves **stop-loss first** —
  documented as a conservative modeling convention, not a fact OHLC data
  can actually establish. Implemented in a new sibling function,
  `resolve_stop_take_profit_ohlc` (`src/orion/backtest/execution.py`),
  never a modification of `orion.paper.execution.resolve_stop_take_profit`
  (which keeps its documented last-price-only v1 behavior — Mission 4
  decision B6 — unchanged for live trading).
- **M6-Q3 (latency):** an engine-level `fill_delay_bars: int = 0`
  parameter, never a `PaperExecutionPolicy` field. `PaperExecutionPolicy`
  stays scoped to cost (spread/slippage/fee); timing is an engine concern.
- **M6-Q4 (persistence):** zero. `BacktestResult` is returned in memory
  only; `orion.backtest` never imports `orion.memory` or `orion.
  reflection` (verified by AST-level import inspection, not a docstring
  grep, in the independent adversarial probe below). A caller wanting
  Reflection or durability over a backtest journals the result's contents
  into a disposable journal themselves — mixing simulated and real trade
  history in the production journal would corrupt Mission 5's "actual
  outcome" narrative, the sharpest integrity risk identified during the
  investigation, closed here by prohibition rather than a guard rail.
- **M6-Q5 (scope):** `run_backtest` is the one reusable primitive; no
  walk-forward/regime/sensitivity/stress wrapper exists in v1 — those are
  future callers of the same function with different `start`/`end`/policy
  arguments, not new subsystems.

**`orion.backtest.engine.run_backtest`** performs one up-front
`validate_snapshot` call (using the dataset's own last-bar timestamp as
`now`, so only EMPTY/GAP/NON_MONOTONIC/DUPLICATE_TIMESTAMP findings can
fire — staleness is checked per-cycle downstream, trivially satisfied by
construction), then loops bar-by-bar: `_window_for(bars, i, window_bars)`
builds a rolling lookback that structurally cannot include `bars[j]` for
`j > i` (no runtime check needed — it is a slice bound, proved directly by
an adversarial test that runs two datasets sharing an identical bar prefix
and diverging afterward, asserting the shared-prefix cycles/equity points
are byte-identical regardless of the diverging future). No warmup-skip
heuristic was needed: `orion.decision.rules.trade_requires_minimum_history`
already downgrades a thin-history trade proposal to WAIT, confirmed live
before writing any code, so the engine evaluates every bar from index 0.
Exactly one state-producing call happens per bar (`execute`/
`execute_delayed`/`resolve_stop_take_profit_ohlc`, each already internally
calling `mark_to_market` once) — an early design sketch that additionally
called `mark_to_market` a second time after `execute()` was caught and
corrected before any test was written.

**Two integration bugs found and fixed during implementation, before any
commit — both would have broken the M6-Q3 latency feature outright:**

1. `orion.paper.execution.execute` (the Mission 4 HIGH-fix from the
   immediately preceding integration audit) requires the exact
   `PortfolioState` `verdict.portfolio_ref` recorded — `portfolio_state_
   fingerprint(portfolio) == verdict.portfolio_ref.content_hash`. The
   engine's first draft called `mark_to_market` on the pending account
   every bar while a delayed fill was in flight, which advances `as_of`
   and therefore silently changes that fingerprint, making every delayed
   fill fail closed with a `PaperExecutionError` at the fill bar. Fixed by
   never touching `portfolio` while a fill is pending — it is provably
   still flat (no open position, so `equity == cash` cannot move with
   price regardless), so leaving it byte-identical to what `review()`
   reviewed is both correct and sufficient; only the equity curve's own
   `as_of` advances.
2. `execute()` also requires `evidence_fingerprint(evidence) == proposal.
   evidence_ref.content_hash` — the *same* Evidence the proposal was
   reviewed against. A delayed fill must price off a *later* bar's
   Evidence by definition, which can never satisfy that check. Rather than
   relax an already-audited Mission 4 contract (explicitly out of scope
   per the user's implementation rules), a new sibling, `execute_delayed`
   (`orion.backtest.execution`), reimplements every other check `execute()`
   performs — proposal/verdict linkage, action/direction consistency, and
   critically the same portfolio-fingerprint check — and deliberately
   omits only the evidence-linkage check, replaced with a narrower
   same-symbol check. Verified directly: a test proves `execute()` itself
   still rejects the later Evidence `execute_delayed` accepts, so the two
   are not redundant.

A third bug, found only by the independent adversarial probe pass (not the
pytest suite): `orion.backtest.metrics._period_returns` paired consecutive
`EquityPoint`s via `zip(equity_curve, equity_curve[1:], strict=True)` —
`strict=True` demands equal-length iterables, but the second is *always*
exactly one element shorter than the first by construction (that is what
pairs each point with its predecessor), so every call with two or more
points raised `ValueError` unconditionally. `sharpe_ratio`/`sortino_ratio`
were completely broken for any real input; the pytest suite's own fixtures
happened not to exercise the failing path directly enough to surface it
before the probe did. Fixed to `strict=False`, with the reasoning recorded
inline against a future edit re-adding `strict=True` out of habit.

**`orion.backtest.metrics`** — `expectancy`, `profit_factor`, `exposure`,
`sharpe_ratio`, `sortino_ratio` — raises `BacktestMetricsError` rather than
returning `NaN`/`inf`/a fabricated `0.0` for every degenerate input (zero
trades, zero return variance, a zero-equity base, zero gross loss, zero
downside deviation), matching the "never silent" discipline already
applied to `orion.risk.portfolio.compute_position_size` and `orion.
backtest.data.HistoricalDataError`. `risk_free_rate_per_period`/
`periods_per_year` are required, no-default parameters, matching `RiskPolicy.
min_risk_reward`'s "no guessed constants" precedent.

**Adversarial verification, beyond the pytest suite:** a standalone probe
script independently confirmed — divergent-future-bars no-look-ahead proof
(two genuinely different `HistoricalBarSource` instances sharing an
identical 70-bar prefix); a 400-bar choppy-market run producing a real
trade with a computable, finite Sharpe/Sortino/exposure; every module's
actual AST-parsed imports (not a docstring substring match, which
initially false-positived on this entry's own prose) confirming zero
`orion.memory`/`orion.reflection` imports anywhere in the package; cash
never negative across a long run; and determinism holding across two
independently-constructed but value-identical datasets, not merely two
calls sharing one object.

**Files added:** `src/orion/backtest/{__init__.py, data.py, execution.py,
schemas.py, metrics.py, engine.py}`, `tests/backtest/{__init__.py,
conftest.py, test_data.py, test_execution.py, test_metrics.py,
test_engine.py}` (67 new tests). No existing source file modified.

**Verification:** `pytest` collects 595 tests, all passing (up from 528).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass
with zero findings.
