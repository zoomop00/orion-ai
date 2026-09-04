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

---

## 2026-08-24 — Mission 7 (Research) v1: economic calendar data layer only

**Decision: a new package, `src/orion/research/`, implementing exactly the
first bullet of the brief's four-item Mission 7 list (`ORION_MASTER_
ENGINEERING_BRIEF_V2.md` §13 — news, economic calendar, macro data,
sentiment) — the economic calendar — behind a Finnhub-backed
`EconomicCalendarProvider` ABC, and nothing else.** News, macro data, and
sentiment are not implemented. No change to `orion.analysis`, `orion.
reasoning`, `orion.decision`, `orion.risk`, `orion.paper`, `orion.memory`,
`orion.reflection`, or `orion.backtest` — this is a standalone data layer,
mirroring exactly where `orion.data` sat before Mission 1 wired it into
`orion.analysis`. Wiring economic calendar events into Evidence/Reasoning is
explicitly a future task, not part of this entry.

**Shape mirrors `orion.data` deliberately, one level down:**
`EconomicCalendarEvent` / `EconomicCalendarSourceMeta` / `EconomicCalendarSnapshot`
(`orion.research.models`) parallel `OHLCVBar` / `DataSourceMeta` /
`MarketSnapshot` exactly — frozen, `extra="forbid"` (the newer, stricter
convention every schema after Mission 1 uses; `orion.data.models` predates it
and was left unchanged, out of scope here). `EconomicCalendarProvider`
(`orion.research.provider_base`) mirrors `orion.data.provider_base.
MarketDataProvider` exactly: one abstract `fetch_calendar(start, end) ->
EconomicCalendarSnapshot`, and `EconomicCalendarProviderError` that must never
be caught and downgraded to fabricated/partial data. `FinnhubEconomicCalendarProvider`
(`orion.research.finnhub_provider`) mirrors `TwelveDataProvider` exactly:
private pydantic response models for the raw provider payload, `_to_event`
raising on the first malformed field (never skipping a bad event or
substituting a guessed default — external research data is untrusted, and a
partially-fabricated calendar is worse than none), and `retrieved_at =
datetime.now(UTC)` read exactly once, only at this live I/O boundary — no
other file in `orion.research` reads the wall clock (verified by an
AST-based adversarial probe, not just code review).

**No new hashing mechanism.** `orion.provenance.canonical_hash` is not
wired into `orion.research` yet (no fingerprint field on any of the three new
models) because nothing downstream consumes one in this milestone —
adding a fingerprint with no caller would be exactly the "building ahead of
proven need" this project has repeatedly declined to do at prior mission
boundaries (Mission 5's deferred-persistence entry is the closest precedent).
Confirmed by direct probe, not just absence of code, that `canonical_hash`
already works correctly over `EconomicCalendarSnapshot` with no changes
needed to either module when a future caller (most likely a Mission 2
`DecisionJournal` extension, or an Evidence-layer integration) does need one
— reproducible, and changes when content changes.

**Calendar-specific validation, not a copy of `orion.data.validation`.**
`orion.research.validation.validate_calendar_snapshot` mirrors
`validate_snapshot`'s shape (a `CalendarValidationResult` of typed findings,
`now` a required caller-supplied parameter, never `datetime.now()` read
internally) but not its content — an economic calendar and a bar series have
different failure modes. Four issue types: `DUPLICATE_EVENT` (same
`(event_time, country, event_name)` more than once), `OUT_OF_RANGE` (an
event outside the provider's own claimed `[query_start, query_end]` window —
catches a provider silently returning data for the wrong dates), `STALE_
RETRIEVAL` / `FUTURE_RETRIEVAL` (retrieval timestamp integrity, the calendar
analogue of `evidence_is_fresh`'s staleness check plus the Risk Guardian's
existing "future timestamp" checks). **`EMPTY` is deliberately non-blocking**
— the one real judgment call in this entry, and the reason it's called out
here rather than left implicit: unlike an OHLCV snapshot (which should never
legitimately be empty — the market always has a most-recent bar, so `orion.
data.validation.DataQualityIssue.EMPTY` is correctly always-invalid), a
narrow economic-calendar query window can be genuinely, correctly empty (a
quiet day with nothing scheduled). Treating it as blocking would falsely
invalidate a large fraction of real, correct provider responses; it is still
recorded as a finding so a caller can distinguish "confirmed quiet" from
"never checked."

**Fail-closed, verified adversarially beyond the pytest suite:** a
standalone probe fed the Finnhub provider nine distinct malformed-response
shapes (null/unparseable/wrong-case fields, a null or non-list
`economicCalendar`, a missing key entirely) and confirmed every one raises
`EconomicCalendarProviderError` — none silently drops the bad event or
substitutes a default. The probe also confirmed: `[query_start, query_end]`
boundaries are inclusive (an event exactly at either edge is not
`OUT_OF_RANGE`, matching every other inclusive-range convention already in
this codebase, e.g. `HistoricalBarSource.fetch_range`); `now == retrieved_at`
never false-positives `FUTURE_RETRIEVAL`; validation is deterministic for
identical input; and zero `anthropic`/`sqlalchemy`/downstream-`orion.*`
imports exist anywhere in the package (AST-parsed, the same static-proof
technique `tests/risk/test_import_hygiene.py` established).

**Config:** `Settings` gained `finnhub_api_key`/`finnhub_base_url`
(`FINNHUB_API_KEY`/`FINNHUB_BASE_URL`, matching `TWELVE_DATA_API_KEY`/
`TWELVE_DATA_BASE_URL`'s pattern exactly), additive to `.env.example`. No
other `Settings` field touched.

**Known limitations, deferred by design, not oversight:** news, macro data,
and sentiment (the brief's other three Mission 7 bullets) are entirely
unbuilt. Zero persistence — a `EconomicCalendarSnapshot` is returned to the
caller only, never journaled (no `orion.memory` change); revisit once a real
consumer needs durability, per the same "don't design storage ahead of a
proven need" precedent Mission 5 already used. No wiring into `orion.
analysis`/`Evidence` — the AI reasoning layer does not see calendar data yet.
No source-reliability/cross-provider corroboration model (only one provider
exists). No data-quality *action* (nothing consumes a `CalendarValidationResult`
yet to decide WAIT-around-news-events or similar — that is a future
`orion.risk`/`orion.decision` integration, explicitly out of scope here).

**Files added:** `src/orion/research/{__init__.py, models.py,
provider_base.py, validation.py, finnhub_provider.py}`,
`tests/research/{__init__.py, test_models.py, test_validation.py,
test_finnhub_provider.py, test_import_hygiene.py}` (37 new tests). **Files
modified (additive only):** `src/orion/config.py`, `.env.example`,
`docs/DECISIONS.md` (this entry).

**Verification:** `pytest` collects 623 tests, all passing (up from 595).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass with
zero findings. `git diff --stat` confirms exactly two pre-existing files
touched, both additive (`.env.example` +4, `src/orion/config.py` +4), and
zero changes to any Mission 0–6 source file.

---

## 2026-08-24 — Mission 8 (Intelligence Team) v1: Macro Analyst only, News
Analyst deferred

**Decision: a new package, `src/orion/analysts/`, implementing exactly one
of the brief's eight Mission 8 roles (`ORION_MASTER_ENGINEERING_BRIEF_V2.md`
§14 — Technical Analyst, News Analyst, Macro Analyst, Bull Researcher, Bear
Researcher, Risk Manager, Portfolio Manager, ORION Coordinator) — the Macro
Analyst — behind a `MacroAnalystProvider` ABC, and nothing else.** News
Analyst is explicitly deferred: no news data provider exists anywhere in
this codebase (Mission 7 v1 shipped only the economic calendar), and
building a News Analyst against no real source would mean either fabricated
input (forbidden by the brief's own Quality Rules) or an ungoverned
data-layer expansion disguised as "just the analyst." The other six roles
(Technical Analyst, Bull/Bear Researcher, Risk Manager, Portfolio Manager,
Coordinator) are entirely out of scope for this entry. This mirrors the
exact precedent the Mission 7 entry above already set — narrowing a
multi-item brief list down to the single smallest safe slice, and recording
that narrowing explicitly rather than leaving it implicit.

**Zero integration, by design, verified mechanically.** Macro Analyst
output is advisory only. Nothing in `orion.analysts` is wired into `orion.
analysis.Evidence`, `orion.decision`, `orion.risk`, `orion.paper`, `orion.
memory`, `orion.reflection`, or `orion.backtest` — no new field, no new
rule, no new parameter anywhere in those packages. `tests/analysts/test_
import_hygiene.py` proves both directions by AST parsing (not a
`sys.modules` runtime check): `orion.analysts` itself never imports any of
those seven packages or `sqlalchemy`, and no Mission 0–7 package imports
`orion.analysts`. Zero persistence: `MacroAnalystOutput` is returned to the
caller only, matching Mission 7's and Mission 5's identical "don't build
storage ahead of a proven consumer" precedent.

**Two-model split, mirroring `orion.reasoning.schemas` exactly.**
`MacroAnalystVerdict` (`src/orion/analysts/schemas.py`) is exactly the JSON
schema sent to Claude as `output_format` — closed enums, bounded scalars,
`extra="forbid"`. `MacroAnalystOutput(MacroAnalystVerdict)` adds provenance
(`model`, `prompt_version`, `input_fingerprint`, `source_provider`,
`query_start`/`query_end`/`retrieved_at`, `analyzed_at`) stamped by
`ClaudeMacroAnalystProvider` in Python — never asked of or reported by the
model, for the same reason `ReasoningOutput.model`/`.prompt_version` are
provider-observed: the model has no reliable way to know them and every
incentive to guess. `input_fingerprint` reuses `orion.provenance.
canonical_hash` directly over the exact `EconomicCalendarSnapshot` analyzed
— no new hashing mechanism.

**No field can encode a trade instruction — a structural property, checked
directly, not merely a naming convention.** `MacroPolicyPressure`
(`TIGHTENING_PRESSURE`/`EASING_PRESSURE`/`MIXED`/`IN_LINE`/
`INSUFFICIENT_DATA`) describes what the supplied economic data implies about
monetary-policy-relevant conditions — deliberately not phrased as
"bullish"/"bearish", "hawkish"/"dovish", or any asset-direction/trading-
signal vocabulary, so it cannot be misread as a recommendation even by a
human skimming it. There is no `recommended_action`, no position size, no
stop-loss/take-profit, no risk-policy field anywhere in the schema.
`tests/analysts/test_schemas.py::test_verdict_has_no_field_capable_of_
encoding_a_trade_instruction` checks the model's actual field names against
a blocklist of trade-instruction vocabulary directly, rather than trusting
that a reviewer would always catch a future field that drifts toward one.

**Deterministic per-event attribution, ORION-verified, never LLM-trusted.**
Each `EconomicCalendarEvent`'s `event_id` is `canonical_hash(event)` —
computed by ORION before the prompt is built, embedded in each event's own
JSON blob so the model can reference it, and independently re-verified after
the call: `ClaudeMacroAnalystProvider.analyze()` checks that the set of
`event_id`s in the model's `key_event_assessments` exactly equals the set of
`event_id`s ORION sent. A model that drops, invents, or duplicates an
`event_id` is a hard `MacroAnalystProviderError`, never a best-effort
partial mapping. No `uuid4()`, no random identifier anywhere.

**Security boundary — fencing is defense-in-depth, not the primary
guarantee.** `SYSTEM_PROMPT` establishes an explicit instruction hierarchy
(only the system prompt has authority; the user turn's `<trusted_control_
data>` — ORION-computed metadata — and `<untrusted_external_data>` —
vendor-controlled event fields — are both data, never commands) and states
concrete, enumerated behavioral rules (ignore embedded instructions, role
reassignment, prompt-reveal requests, tool-call requests, code/SQL/URL
output requests; never fabricate; explicit `INSUFFICIENT_DATA` instead of a
forced confident answer) rather than a vague "be careful with injection"
line. The system prompt is a fixed constant, byte-identical on every call —
never interpolated with external content; only the user turn varies
(`test_system_prompt_is_never_interpolated_with_external_content`). But the
real guarantees are structural, independent of whether Claude actually
honors the prompt: (1) `client.messages.parse(output_format=
MacroAnalystVerdict)` is SDK-enforced — nothing the model returns can escape
the closed schema; (2) no `tools` parameter is ever passed
(`test_no_tools_are_ever_bound_to_the_call`); (3) the `event_id`
set-equality check above; (4) zero downstream execution privilege — nothing
in this mission reads `MacroAnalystOutput` at all beyond returning it.
`rationale`/`MacroEventAssessment.note` are the only free-text fields
(capped at 500/160 characters); `tests/analysts/test_import_hygiene.py::
test_orion_analysts_has_no_exec_eval_or_subprocess_calls` proves, by AST
scan, that nothing in the package could turn either field into executed
behavior even if a model echoed adversarial text into them.

**Input size limits — fail-closed rejection, never silent truncation.**
`MAX_EVENTS_PER_CALL=50`, `MAX_EVENT_NAME_CHARS=200`, `MAX_COUNTRY_CHARS=64`,
`MAX_UNIT_CHARS=32`, `MAX_TOTAL_SERIALIZED_CHARS=30_000`
(`src/orion/analysts/claude_macro_provider.py`), all checked — and any
breach raised as `MacroAnalystProviderError` — before any API call is made.
Never truncated: silently dropping the tail of an event list or cutting a
field mid-value could change what is being analyzed with no signal that
anything was cut, which this codebase treats as strictly worse than
refusing to run (the same reasoning `orion.backtest`/`orion.paper` already
apply to malformed data). **Mission 7's `EconomicCalendarEvent.event_name`/
`country`/`unit` have no upper length bound of their own** — these caps are
entirely this provider's own responsibility, not inherited from Mission 7
(confirmed directly: `test_oversized_event_name_raises_before_any_client_
call` constructs an over-length `event_name` via ordinary, successful
`EconomicCalendarEvent` construction). The total-size cap is not redundant
with the per-field caps: a string within its own character limit can still
balloon past that many bytes once JSON-escaped, so it is an independent
backstop against escape expansion, verified in isolation via
`monkeypatch` rather than relying on naturally exceeding it with realistic
inputs.

**Exception handling written more conservatively than the existing Mission
1 precedent it mirrors.** `ClaudeMacroAnalystProvider` catches the same
`anthropic.*Error` hierarchy `ClaudeReasoningProvider` already does, in the
same order, but two branches (`BadRequestError`, `APIConnectionError`) build
their message from a known-safe structured field (`status_code`) or a fixed
string instead of interpolating the caught exception's raw `str()` — `orion.
reasoning.claude_provider` does interpolate raw exception text in those two
branches today, which was not touched (out of scope for this mission), but
the new file does not repeat that pattern. This is a defensive precaution,
not a confirmed vulnerability: the Anthropic API authenticates via an
`x-api-key` header, not a URL query parameter, so the exact credential-in-
URL leak fixed for `orion.data.twelvedata_provider`/`orion.research.
finnhub_provider` (see the two entries above) should not reproduce here —
recorded as a known, unconfirmed-but-mitigated risk rather than silently
assumed safe.

**Model/effort configuration reused unchanged.** `Settings.anthropic_model`
/ `Settings.anthropic_effort` — no new settings field, no new `.env`
variable. No concrete architectural reason exists yet to give analysts a
separate model/effort pair. Currently approved configuration: Claude Opus,
effort High.

**Independent Mission 6 hardening, unrelated to Mission 8's own scope:**
`tests/backtest/test_import_hygiene.py` did not exist before this entry —
every other network/vendor-adjacent package (`risk`, `paper`, `memory`,
`research`) already had an AST-based import-hygiene test; `orion.backtest`
did not, despite its own module docstring already asserting "never imports
`orion.memory` or `orion.reflection`." Added now, mirroring the existing
four precedents exactly, disallowing network/broker/AI-vendor imports,
`sqlalchemy`, `orion.memory`, `orion.reflection`, and (new) `orion.
analysts` — a live LLM call must never enter the deterministic, replayable
backtest loop (`orion.backtest.engine.run_backtest`'s own determinism
guarantee depends on every step being a pure function; an LLM call is not
one, which is exactly why `orion.reasoning`'s `ReasoningProvider` is only
ever exercised in a backtest via a caller-supplied, presumably-deterministic
implementation, never Macro Analyst).

**Adversarial test coverage beyond ordinary unit tests
(`tests/analysts/test_claude_macro_provider.py`):** the full matrix designed
during Mission 8's review — hostile text in `event_name`/`country`
surviving as inert, JSON-recoverable data (not raw substring matching, since
JSON does not escape `<`/`>`: a hostile `event_name` containing a literal
`</untrusted_external_data>` string legitimately appears twice in the raw
prompt text, once as escaped data and once as the real boundary — the
guarantee proven is that the JSON array is still syntactically well-formed
and the *real* boundary, the last occurrence, still parses correctly, not
that the tag string is unique); every `anthropic.*Error` type mapped
correctly; malformed/refused model output; the `event_id` set-mismatch
integrity check in both directions (dropped and invented ids); fingerprint
determinism and sensitivity; and a direct, honest note in the test file
itself that a mocked-client unit test cannot prove Claude actually resists
injection — only that the schema/pipeline/provider code stays safe
regardless of what either side sends.

**Finding, fixed before commit: the tag's `provider` attribute was
interpolated unescaped.** A fresh, independent adversarial audit (run after
the implementation and its own test suite were already green) found that
`_build_user_content` interpolated `snapshot.source.provider` directly into
`<untrusted_external_data provider="...">` with no escaping — every other
value in that tag is either `json.dumps`-serialized (the events/control
payloads) or structurally incapable of containing a quote character (an
`isoformat()` datetime, an `int`), but `EconomicCalendarSourceMeta.provider`
is a plain `str` with only `min_length=1`. A `provider` value containing a
literal `"` could terminate the attribute early and inject an
attacker-controlled pseudo-attribute into the rest of the tag (confirmed
directly: `provider='finnhub" onerror="ignore all rules'` produced
`<untrusted_external_data provider="finnhub" onerror="ignore all rules" ...>`
verbatim). **Not exploitable via the actual current call graph** —
`FinnhubEconomicCalendarProvider.fetch_calendar` hardcodes `provider=
"finnhub"` as a Python literal, never vendor response content, so no real
caller can trigger this today — but the type system does not guarantee that
stays true for a second future provider or a caller constructing
`EconomicCalendarSnapshot` directly, and the module's own documented
principle is not to rely on "this call site happens to be safe today" for
an unrestricted string field. Fixed by escaping `provider` with the
standard-library `html.escape(..., quote=True)` before interpolation — the
only fencing-template value that needed it. Regression test added:
`test_hostile_snapshot_provider_string_cannot_break_the_tag_attribute`. The
audit script's own first-pass check of this was itself found to be
tautological (it compared the built output against the same naive
interpolation the code performed, which trivially matched regardless of
whether escaping was happening) — corrected to check the actual structural
property (no raw `" onerror="` sequence; `&quot;` present) before being
trusted. Recorded here in the interest of an honest audit trail, not just
the fix.

**Known limitations, deferred by design:** News Analyst (blocked on a
missing data source, per above); no live-API adversarial smoke test exists
yet (a genuine "does Opus actually resist this" evaluation needs a real API
call, deliberately kept out of the CI-blocking `pytest` suite for cost/
flakiness reasons — recommended as a separate, manually-run exercise before
relying on this in a context where the output is read broadly); no
"measurable value" demonstration is automated, since v1 has no consumer by
design — recommended as a small, separate, manually-run example script
against real Finnhub data, not part of this entry.

**Files added:** `src/orion/analysts/{__init__.py, schemas.py,
provider_base.py, claude_macro_provider.py}`, `tests/analysts/{__init__.py,
test_schemas.py, test_claude_macro_provider.py, test_import_hygiene.py}`,
`tests/backtest/test_import_hygiene.py`. No existing source file modified.

**Verification:** `pytest` collects 732 tests, all passing (up from 649).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass with
zero findings.

---

## 2026-08-24 — Mission 9 (Voice) Phase 1: schema + provider-contract only,
zero vendor, zero integration

**Decision: a new package, `src/orion/voice/` (`__init__.py`, `schemas.py`,
`provider_base.py`), establishing only the stable contract a future voice
runtime and concrete STT/TTS providers will build upon.** Nothing else. This
is Phase 1 of Mission 9 (`ORION_MASTER_ENGINEERING_BRIEF_V2.md` §15 —
`IDLE → LISTENING → UNDERSTANDING → ANALYZING → SPEAKING → IDLE`), not a
working voice interface — see the read-only architecture investigation that
preceded this entry for why: no orchestrator, no CLI, no web layer, and no
audio I/O of any kind exists anywhere in this codebase (confirmed fresh —
`src/orion/cli.py` does not exist despite being referenced in
`pyproject.toml`'s `[project.scripts]`), and every mission since Mission 2
has explicitly and repeatedly deferred building one. The `UNDERSTANDING`/
`ANALYZING` states cannot be made coherent without that orchestrator; this
phase deliberately does not attempt to.

**Two separate ABCs, not one "VoiceProvider" with a mode.**
`TranscriptionProvider` (speech → text) and `SpeechSynthesisProvider` (text →
speech), each with its own error root (`TranscriptionProviderError`,
`SpeechSynthesisProviderError`), mirroring the "one ABC per concern"
precedent already used seven times in this codebase (`MarketDataProvider`,
`EconomicCalendarProvider`, `ReasoningProvider`, `MacroAnalystProvider`,
`HistoricalBarSource`, `PortfolioStore`, `DecisionJournal`). Both ABCs live
in one `provider_base.py` file only because the approved Phase 1 file list
keeps `orion.voice` to three files; they remain independently named
contracts a single vendor need not implement both halves of.

**`TranscriptionProvider.transcribe(audio: bytes) -> Transcript`** —
vendor-neutral by taking opaque raw bytes in, with no assumption about
container format, sample rate, or encoding; a concrete provider adapts
whatever conversion its own vendor API needs, the same way
`TwelveDataProvider.fetch_ohlcv` adapts a raw JSON response into
`MarketSnapshot` without the ABC knowing about either shape.
**`SpeechSynthesisProvider.synthesize(text: str) -> SynthesizedSpeech`** —
`SynthesizedSpeech` is the smallest vendor-neutral shape for TTS output
(`audio: bytes`, `format: str`, plus provenance); `format` is a bounded
string, not a closed enum, since supported encodings vary by vendor and are
not guessed here.

**`Transcript` — the model-authored-equivalent half of the contract, but
provider-authored, not model-authored** (there is no LLM in this phase).
Frozen, `extra="forbid"`, matching every schema in this codebase. `text` is
the only free-text field and is explicitly treated as **untrusted data**
from the moment it exists — nothing in this package interprets it as an
instruction, and there is deliberately no `command`/`intent` field:
assigning meaning to transcript text is the eventual UNDERSTANDING layer's
job, a separate, not-yet-designed security boundary this phase does not
attempt to solve (see `orion.analysts.claude_macro_provider` for the
fencing/closed-schema/no-tools pattern that layer will need to follow).
`text` additionally rejects non-printable/control characters
(`str.isprintable()`), mirroring `orion.analysts.claude_macro_provider.
_validate_provider_field`'s identical "reject, never silently normalize"
discipline from the Mission 8 hardening follow-up — both a data-quality
check (real STT output is natural language) and defense-in-depth for
whatever future context eventually embeds this text.

**No session/utterance identifier field.** A `Transcript` is content-
addressed via `transcript_fingerprint(transcript)` — a thin wrapper around
`orion.provenance.canonical_hash`, mirroring `decision_fingerprint`/
`risk_verdict_fingerprint`/`portfolio_state_fingerprint` exactly, including
why it is a standalone function and never a field on `Transcript` itself (a
self-referencing hash field would need to exclude itself from its own
input, which `canonical_hash` does not special-case for any model). This is
the same "content fingerprint as identity" precedent Mission 2's entire
journal design already established, and it is what makes a caller-supplied,
`uuid4()`-minted identifier unnecessary — there is no orchestrator yet to be
a minting authority for one anyway (the same reason `analysis_id` in
`orion.decision.engine.decide` is caller-supplied, never internally minted).

**Zero `datetime.now()` anywhere in this phase.** `Transcript.transcribed_at`
/`SynthesizedSpeech.synthesized_at` are required, timezone-enforced fields
with no default — a future concrete provider stamps them at its own live
I/O boundary, exactly like `FinnhubEconomicCalendarProvider.retrieved_at`/
`ClaudeMacroAnalystProvider.analyzed_at`, but no such boundary exists yet in
Phase 1, so no wall-clock read exists yet either.

**Finding, fixed before commit: `duration_seconds` did not exclude
infinity.** A fresh adversarial probe (run after the implementation and its
own test suite were already green) found that `Field(gt=0)` alone does not
reject `float("inf")` — `inf > 0` is `True` in Python, so a malformed
provider response reporting an infinite-duration clip would have
constructed successfully (`NaN` was already correctly excluded, since NaN
comparisons are always `False`). No real audio clip has infinite duration.
Fixed with an explicit `math.isfinite()` field validator on
`duration_seconds`. The same probe pass also confirmed, empirically rather
than assumed, that the existing `text` control-character check already
correctly rejects the RIGHT-TO-LEFT OVERRIDE character (U+202E, a known
homoglyph/text-spoofing vector) — `str.isprintable()` treats Unicode's
entire "Other" major category (`Cc`/`Cf`/`Cs`/`Co`/`Cn`), not just `Cc`
("control"), as non-printable, so this was already covered without
additional code. Recorded here in the interest of an honest audit trail,
matching the same discipline the Mission 8 entry above already established.

**Zero new dependencies, zero persistence, zero vendor.** No entry in
`pyproject.toml` changed. No SQLite table, no journal method, no `orion.
memory` integration — no proven consumer exists (matching Mission 5/7/8's
identical precedent), and voice recordings/transcripts are plausibly
biometric/PII data under some privacy regimes, a materially new sensitivity
class this codebase has not had to consider before, reinforcing rather than
merely repeating the "no persistence without a proven need" precedent.
Concrete STT/TTS vendor selection is deliberately deferred to a separate,
later decision — current-ecosystem research (Deepgram, ElevenLabs, OpenAI,
Google Cloud, Azure, Amazon Polly) was done during the investigation phase,
but no vendor is picked here.

**Zero pipeline integration, mechanically proven.**
`tests/voice/test_import_hygiene.py` proves, by AST parsing (not a
`sys.modules` runtime check): `orion.voice` imports no Mission 0–8 pipeline
package (`orion.data`/`analysis`/`research`/`reasoning`/`analysts`/
`decision`/`risk`/`paper`/`memory`/`reflection`/`backtest`) and no network/
AI-vendor/persistence module (the exact `DISALLOWED_TOP_LEVEL_MODULES` set
`tests/backtest/test_import_hygiene.py` already uses, reused rather than
reinvented); no Mission 0–8 package imports `orion.voice`; and — called out
with its own dedicated test, per the explicit brief — `orion.backtest`
specifically cannot import `orion.voice`. This is not an accidental-coupling
guard: a live voice interaction is inherently non-deterministic (real-time
audio, live vendor calls), and `run_backtest`'s determinism/no-look-ahead
guarantee depends on every step being pure or a caller-supplied,
presumably-deterministic implementation — the same structural
incompatibility that already keeps `orion.analysts` out of backtest.
`orion.provenance` is the one exception to the "no Mission-package import"
rule, deliberately — it is a shared, dependency-free top-level utility, not
a Mission-specific pipeline package, imported the same way `orion.analysts`/
`orion.decision`/`orion.risk` already import it.

**Known limitations, deferred by design, not oversight:** no concrete STT/
TTS provider (vendor selection is a separate decision); no orchestrator, no
`UNDERSTANDING`/`ANALYZING` state wiring, no live audio I/O, no microphone/
speaker capture, no streaming, no interruption handling, no sensitive-
command confirmation protocol (that needs a real command surface to confirm
against, which does not exist yet), no CLI, and `src/orion/cli.py`'s broken
`[project.scripts]` reference remains unfixed — all explicitly out of this
phase's scope, not silently skipped.

**Files added:** `src/orion/voice/{__init__.py, schemas.py,
provider_base.py}`, `tests/voice/{__init__.py, test_schemas.py, test_import_
hygiene.py}`. No existing source file modified.

**Verification:** `pytest` collects 803 tests, all passing (up from 757).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass with
zero findings.

## 2026-08-24 — Mission 9 (Voice) Phase 2: Deepgram Nova-3/Aura-2 concrete
providers, still zero pipeline integration

**Decision: two concrete providers, `orion.voice.deepgram_stt.
DeepgramTranscriptionProvider` and `orion.voice.deepgram_tts.
DeepgramSpeechSynthesisProvider`, implementing Phase 1's `TranscriptionProvider`/
`SpeechSynthesisProvider` ABCs against Deepgram's REST API (Nova-3 for STT,
Aura-2 for TTS) — the single vendor approved by the preceding read-only
investigation, chosen in part for Nova-3's documented Hindi/Hinglish
code-switching support.** Everything else from Phase 1 remains unchanged and
still out of scope: no live microphone/speaker I/O, no streaming, no
interruption/barge-in, no orchestrator, no UNDERSTANDING/LLM layer, no
pipeline wiring (Evidence/Decision/Risk/Paper/Memory/Reflection/Backtest).

**Deepgram REST, not the `deepgram-sdk` package.** The official Python SDK
(`deepgram-sdk==7.7.0` on PyPI) was installed and inspected (`uv add
deepgram-sdk`, then `dir(deepgram)`): it is a large, Fern-generated client
covering Deepgram's entire product surface (agent configuration, billing,
project/member management, voice-agent orchestration, streaming) of which
STT/TTS is a small fraction, and it pulls in `websockets` as a transitive
dependency this codebase has no other use for. This does not match how every
other REST vendor in this codebase is integrated (`TwelveDataProvider`,
`FinnhubEconomicCalendarProvider`: plain `httpx` calls against the documented
REST endpoints) — only `anthropic` gets a vendor SDK, because
`ClaudeReasoningProvider`/`ClaudeMacroAnalystProvider` depend on its
structured-output parsing (`messages.parse`), which a hand-rolled REST call
cannot replicate. Deepgram's REST contract for pre-recorded STT (`POST
/v1/listen`) and TTS (`POST /v1/speak`) is simple enough to call directly
with `httpx`, so the dependency was removed again (`uv remove deepgram-sdk`)
in favor of matching the Finnhub/Twelve Data precedent: smaller attack
surface, one dependency total (`httpx`, already present) instead of a large
generated SDK plus `websockets`, and direct reuse of `orion.http_errors`
(written generically enough to already cover header-auth vendors — see its
module docstring) instead of an unaudited SDK's own exception types.

**Header-based auth only, empirically verified rather than assumed safe.**
Both providers send `Authorization: Token <key>` and never place the key in
the URL/query string. Because `orion.http_errors.describe_http_error` was
originally written against `TwelveDataProvider`/`FinnhubEconomicCalendarProvider`'s
query-parameter auth, this phase did not assume the same protection
automatically covers a header-auth vendor: a throwaway script constructed a
real `httpx.Request` with a fake credential header and a resulting
`httpx.HTTPStatusError`, and confirmed empirically that neither
`Request.__repr__`/`__str__` nor `HTTPStatusError.__str__`/`repr()` embed
headers (only scheme/host/path/query, matching `describe_http_error`'s own
design). `tests/voice/test_deepgram_stt.py`/`test_deepgram_tts.py` encode
this as permanent regression tests (parametrized across 400/401/403/404/429/
500/502/503 plus a connection error, mirroring
`tests/research/test_finnhub_provider.py`'s identical pattern) checking
`str()`/`repr()`/`traceback.format_exception()`/`__context__` for the key,
plus a dedicated test asserting the outgoing request's `Authorization`
header carries the key and its URL/query params never do. A second,
independently-written adversarial probe (no shared code with the
implementation's own tests) re-ran the same checks, plus
`logging.exception()` output, malformed-response handling, oversized/empty-
input rejection, and an AST-based scan of the two provider files for
dangerous calls/imports (`exec`/`eval`/`compile`/`subprocess`/`os`,
persistence modules, `random`/`uuid`) — all passed.

**Nova-3 `language=multi`.** Deepgram's documented multilingual
code-switching mode for Nova-3 — the concrete mechanism behind the
Hindi/Hinglish capability that was the stated reason for selecting Deepgram.
Fixed as a module constant, not configurable: Settings gained only
`DEEPGRAM_API_KEY` (`Settings.deepgram_api_key`, `.env.example`), matching
the approved "no unnecessary configuration" scope. Aura-2's voice/model
(`aura-2-thalia-en`) and output encoding (`mp3`) are likewise fixed constants
in `deepgram_tts.py`, sent as explicit request query parameters so
`SynthesizedSpeech.model`/`.format` reflect the actual values used by
construction (a 200 response is Deepgram's REST contract that the requested
model/encoding were honored) rather than a guessed or vendor-default value.

**Fail-closed on every malformed/absent field, no fabrication.** Both
providers validate the vendor response through private Pydantic models
before ever touching `Transcript`/`SynthesizedSpeech`; a response missing
`results.channels`, an empty `alternatives` list, an empty transcript ("no
speech detected" is Deepgram's actual behavior for silence, not an error
status) or empty synthesized audio all raise the documented
`TranscriptionProviderError`/`SpeechSynthesisProviderError` rather than
constructing a placeholder. `Transcript.model` is populated only from
`metadata.model_info` when Deepgram actually reports it in the response —
never fabricated when absent. `MAX_AUDIO_BYTES` (25 MB) and `MAX_TEXT_CHARS`
(2000, matching `Transcript.text`'s existing cap) are enforced before any
vendor request is made — oversized/empty input is rejected outright, never
silently truncated, matching `orion.analysts.claude_macro_provider`'s
identical pre-request size-limit discipline.

**Import boundary tightened, not loosened.** `tests/voice/test_import_
hygiene.py` now allows `httpx` only inside `deepgram_stt.py`/`deepgram_tts.py`
(`test_only_vendor_provider_files_import_httpx`) — `schemas.py`/
`provider_base.py`/`__init__.py` remain vendor-independent, unchanged from
Phase 1. `deepgram` (the SDK package name) was added to the disallowed
top-level module set even though it is not a dependency, as a deliberate
tripwire against a future contributor reintroducing it. A new test
(`test_deepgram_provider_files_do_not_import_orion_config`) confirms both
provider files take `api_key: str` as a constructor parameter rather than
reading `Settings` themselves, matching `FinnhubEconomicCalendarProvider`/
`ClaudeReasoningProvider`'s identical pattern — wiring a provider instance to
`Settings.deepgram_api_key` is orchestrator work, still out of scope.

**Zero persistence, zero pipeline integration, zero live calls in tests.**
No SQLite table, no journal method, no file/audio persistence anywhere in
either provider — confirmed by the same AST-based hygiene tests plus the
independent probe's source-text scan for `open(`/`.write_bytes(`/`pickle`.
All provider tests mock the network via `respx`; no real `DEEPGRAM_API_KEY`
is required to run the suite.

**Known limitations, deferred by design:** no live audio I/O, no streaming,
no interruption handling, no orchestrator, no UNDERSTANDING/LLM
interpretation, no pipeline wiring, no CLI — all explicitly out of Phase 2's
scope per the approved plan, not oversight.

**Files added:** `src/orion/voice/{deepgram_stt.py, deepgram_tts.py}`,
`tests/voice/{test_deepgram_stt.py, test_deepgram_tts.py}`. **Files
modified:** `src/orion/config.py` (`Settings.deepgram_api_key`),
`.env.example` (`DEEPGRAM_API_KEY`), `tests/voice/test_import_hygiene.py`
(vendor-file httpx carve-out plus two new boundary tests). No Mission 0–8
source touched.

**Verification:** `pytest` collects 852 tests, all passing (up from 803).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass with
zero findings. An independently-written adversarial probe (separate from the
implementation's own test files) re-verified credential non-leakage,
malformed/empty-response handling, pre-request size-limit enforcement, and
import/dangerous-call hygiene — all checks passed.

## 2026-08-24 — Mission 9 (Voice) Phase 3: the first real voice runtime
(mic → STT → response → TTS → speaker), still no orchestrator

**Decision: five new files complete the physical loop —
`IDLE → LISTENING → UNDERSTANDING → ANALYZING → SPEAKING → IDLE` — as one
composable `VoiceRuntime` (`orion.voice.runtime`) built entirely from ABCs.**
`orion.voice.state.VoiceStateMachine` drives `VoiceState` (unchanged from
Phase 1 — no second competing enum); `orion.voice.audio_input.AudioInput`/
`orion.voice.audio_output.AudioOutput` are the two new Phase 3 ABCs for
local audio I/O; `orion.voice.response.ResponseProvider` is the new
`Transcript -> str` boundary. `VoiceRuntime.run_turn()` composes exactly
five injected dependencies (`AudioInput`, `TranscriptionProvider`,
`ResponseProvider`, `SpeechSynthesisProvider`, `AudioOutput`) and nothing
else — no memory, no database, no tool invocation, no trading logic, no
conversation history across turns, matching Mission 5/7/8's identical "no
proven consumer, no premature integration" precedent extended to voice.

**State machine: a fixed graph, not a general one.**
`_ALLOWED_TRANSITIONS` (`orion.voice.state`) permits only the brief's
sequential edges plus one extra edge out of every non-`IDLE` state, straight
back to `IDLE`. That second edge is the sole, uniform failure-recovery path
— not a special-cased bypass — which is what makes "no runtime path can
leave ORION permanently stuck outside `IDLE`" a structural property of the
graph rather than something every call site has to remember. Any other
transition (skipping a state, moving backward, `IDLE` jumping straight to
`SPEAKING`) raises `InvalidVoiceStateTransitionError` and leaves the state
unchanged — fail-closed, matching every other invariant-violation error in
this codebase. `VoiceStateMachine` is independently unit-tested
(`tests/voice/test_state.py`) with zero providers involved.

**`VoiceRuntime.run_turn()`'s one broad `except Exception`.** Deliberately
wider than this codebase's usual narrow, typed `except` clauses (compare
`orion.voice.deepgram_stt`'s specific `except httpx.HTTPError`) — the
requirement here is stronger than "handle known failure modes": *no*
exception from *any* of the five dependencies, at *any* stage, may leave
`VoiceState` outside `IDLE`. The original exception is always re-raised
unchanged after `reset_to_idle()` — a caller still learns exactly what
failed; only the runtime's own state is protected, never the error
information. Every one of the five failure points (microphone, STT,
response provider, TTS, speaker), plus a malformed-provider-response case
and a reentrancy guard (`VoiceRuntimeBusyError`, raised if `run_turn()` is
called while not `IDLE`), has a dedicated regression test in
`tests/voice/test_runtime.py`.

**Audio abstractions, real and in-memory, in the established pattern.**
`AudioInput.capture() -> bytes` and `AudioOutput.play(speech:
SynthesizedSpeech) -> None` are new ABCs (Phase 1 defined none — no audio
I/O existed yet). Each ships two implementations in the same file as its
ABC, mirroring `InMemoryPortfolioStore`/`InMemoryHistoricalBarSource`'s
established "real, deterministic in-memory implementation next to the ABC"
precedent, not a `unittest.mock` stand-in reinvented per test file:
`InMemoryAudioInput`/`InMemoryAudioOutput` (test/dev doubles) and
`MicrophoneAudioInput`/`SpeakerAudioOutput` (real local hardware via
`sounddevice`). `AudioOutput.play` takes the whole `SynthesizedSpeech`
object, not just `bytes` — `.format` is required to interpret raw PCM at
all, and passing the validated object matches this codebase's established
"pass the object, not a raw field" convention
(`MacroAnalystProvider.analyze(snapshot)`).

**Dependency: `sounddevice` (+ transitively `cffi`/`pycparser`), chosen
after evaluation.** The smallest mature option for numpy-array-based
record *and* playback in one library (PortAudio binding, ~3-package
install, no extra native setup on Windows — its wheel bundles PortAudio).
Alternatives considered and rejected: `pyaudio` (older, more fragile
Windows install story for the same capability), `simpleaudio`
(unmaintained, playback-only — would still need a second library for
capture). The `sounddevice` import is deferred to inside
`MicrophoneAudioInput`/`SpeakerAudioOutput`'s methods, not made at module
level, so merely importing `orion.voice.audio_input`/`audio_output`, or
using the `InMemory*` classes, never requires a working audio backend —
relevant for CI/headless environments where PortAudio may not load even
with the package installed. `pyproject.toml`/`uv.lock` gained exactly this
one dependency; nothing else changed.

**`orion.voice.deepgram_tts` changed: `mp3` → `linear16`/`container=wav`/
`sample_rate=24000`.** The one Phase 2 file this phase modifies (Mission 9,
not Mission 0–8 — in scope). Phase 2's default MP3 output has no
stdlib-only decoder; adding an MP3-decoding dependency purely to support
real local playback was judged a worse trade than changing three Deepgram
TTS request query parameters. `container=wav` produces a complete,
self-describing RIFF/WAVE file (sample rate/channels/bit depth all read
from the file's own header), decoded by `SpeakerAudioOutput` with the
stdlib `wave` module alone — zero new dependency for the container format,
and `SynthesizedSpeech.format` stays a plain string (`"wav"` instead of
`"mp3"`), no new schema field needed. `tests/voice/test_deepgram_tts.py`
was updated to match (`encoding`/`container`/`sample_rate` query-param
assertions, `format == "wav"`). This is an additive record of a follow-up
change, not a rewrite of the Phase 2 decision entry above.

**Response provider: `StaticResponseProvider`, deliberately
content-independent.** Phase 3 does not build the real
`UNDERSTANDING`/`ANALYZING` reasoning layer — that is explicit future
orchestrator work (`ORION_MASTER_ENGINEERING_BRIEF_V2.md` §15, matching
Phase 1's identical deferral). `StaticResponseProvider.respond(transcript)`
returns a fixed string ("Hello. I'm ORION. I heard you, but full reasoning
isn't connected in this build yet.") regardless of `transcript.text`'s
content — never reading it at all, rather than reading it carefully. This
is the strongest available guarantee that untrusted transcript text
"travels through the runtime as inert data": the shipped code path cannot
act on content it never inspects. `tests/voice/test_response.py`
parametrizes several hostile payloads (prompt-injection phrasing, shell/SQL
metacharacters, a trading command) and proves every one produces the
identical response, never echoed. A future transcript-aware provider will
need its own explicit defense (fencing, bounded output, no tool access) the
same way `orion.analysts.claude_macro_provider` already does for untrusted
economic-calendar text — not solved preemptively here.

**Audio limits, enforced before any provider call.**
`MAX_RECORDING_SECONDS` (30s) / `MAX_AUDIO_INPUT_BYTES` (25 MB, matching
`orion.voice.deepgram_stt.MAX_AUDIO_BYTES`'s numeric value but declared
independently — this module has no vendor dependency) on the input side;
`MicrophoneAudioInput` additionally rejects a non-positive or
over-ceiling `duration_seconds`/`sample_rate` at construction, before any
hardware is touched. Oversized/empty audio is rejected outright, never
silently truncated, one layer earlier than Phase 2 already enforces the
same discipline at the STT-provider boundary.

**Zero persistence, in-memory throughout, no temp file.**
`MicrophoneAudioInput` never writes to disk: `sounddevice.rec()` returns a
numpy array directly, and the WAV container is built in an in-memory
`io.BytesIO` buffer via the stdlib `wave` module. `SpeakerAudioOutput`
decodes the same way, in memory, and does not retain played audio after
`play()` returns. `InMemoryAudioOutput.played` (a plain Python list) is a
test/dev convenience, garbage-collected with the object — not persistence
in this codebase's sense of the word (durable storage). No SQLite table, no
journal method, no file write anywhere in the five new files — confirmed by
the AST-based hygiene tests plus an independent adversarial probe's
AST-based scan distinguishing `wave.open(...)` (an `ast.Attribute` call on
an in-memory buffer) from a bare builtin `open(...)` (which would be
filesystem access, and does not appear).

**Import boundary: `runtime.py` depends only on ABCs, checked by name.**
`tests/voice/test_import_hygiene.py` gained
`test_runtime_does_not_import_concrete_vendor_or_hardware_modules`, which
parses `runtime.py`'s own imports and asserts neither `httpx`/`sounddevice`
nor `orion.voice.deepgram_stt`/`orion.voice.deepgram_tts` appear — not
merely that the file "happens not to" today. `sounddevice` was added to
`DISALLOWED_TOP_LEVEL_MODULES`, carved out only for `audio_input.py`/
`audio_output.py`, the same per-file-allowlist mechanism Phase 2 already
established for `httpx`. `uuid`/`random`/`secrets` were added to the
disallowed set too (no file in `orion.voice` has a legitimate reason to
mint an id or generate randomness) — a committed, permanent guarantee,
not just something the adversarial probe happens to check this once.

**Real hardware, honestly scoped.** `MicrophoneAudioInput`/
`SpeakerAudioOutput` were smoke-tested against this development machine's
actual default input/output devices (a 1-second real microphone capture,
and real speaker playback of a locally-generated test tone — not audio
from Deepgram) and both completed without error. **No
`DEEPGRAM_API_KEY` is configured in this environment**, so the full
mic → Deepgram STT → response → Deepgram TTS → speaker loop was *not*
exercised against real vendor APIs — only the local audio I/O layer was
hardware-tested; the vendor-integrated path is verified by mocked/`respx`
tests only. Nothing captured during the smoke test was saved, logged, or
retained beyond the single process that ran it.

**Known limitations, deferred by design:** no voice-activity detection —
`MicrophoneAudioInput` records a fixed `duration_seconds` window and
returns, no early stop on silence; no streaming, no interruption/barge-in
(the brief lists these as Mission 9 features, but building even a minimal
version was judged unnecessary for "prove the physical loop exists," per
this phase's explicit scope); no orchestrator, no real
`UNDERSTANDING`/`ANALYZING` reasoning (that is what
`ResponseProvider`/`StaticResponseProvider` deliberately does not attempt);
no sensitive-command confirmation protocol (still no real command surface
to confirm against); no CLI entry point; no wiring into
Evidence/Decision/Risk/Paper/Memory/Reflection/Backtest. Mission 10's
interface work can visualize this real runtime's real states and real
audio once an orchestrator exists to drive it continuously — Phase 3 was
built specifically so that visualization would have a real state machine
and real audio path to render, not a mock to later replace.

**Files added:** `src/orion/voice/{state.py, audio_input.py,
audio_output.py, response.py, runtime.py}`,
`tests/voice/{test_state.py, test_audio_input.py, test_audio_output.py,
test_response.py, test_runtime.py}`. **Files modified:**
`src/orion/voice/deepgram_tts.py` (encoding/container/sample_rate — see
above), `tests/voice/test_deepgram_tts.py` (matching assertions),
`tests/voice/test_import_hygiene.py` (per-file vendor/hardware allowlist,
`runtime.py` boundary test, `uuid`/`random`/`secrets` ban),
`pyproject.toml`/`uv.lock` (`sounddevice` dependency added). No Mission 0–8
source touched.

**Verification:** `pytest` collects 923 tests, all passing (up from 852).
`ruff check .`, `ruff format --check .`, and `mypy . --strict` all pass with
zero findings (a `[[tool.mypy.overrides]]` entry for `sounddevice.*` was
added, matching the existing `pandas_ta_classic.*` precedent — no published
type stubs). An independently-written adversarial probe (separate from the
implementation's own test files) re-verified hostile-transcript handling,
control-character rejection, oversized/empty audio at both the input and
output boundary, malformed-provider-response handling, all five
failure-recovery paths plus reentrancy, credential non-leakage through the
runtime's own exception plumbing, real-hardware error wrapping, source-level
AST hygiene (no `exec`/`eval`/`compile`/`subprocess`, no persistence, no
randomness, no bare filesystem `open`), and Mission 0–8/backtest isolation
— all checks passed.

---

## 2026-08-27 — Mission 10 Phase 1: the ORION interface prototype

**Decision:** Build the first real ORION interface as a standalone Next.js 16 /
React 19 / TypeScript app in `frontend/`, rendered entirely with CSS, SVG and
native browser APIs. Frontend only: no FastAPI, no WebSocket, no microphone, no
persistence, no network call of any kind.

**Why a separate `frontend/` directory rather than a monorepo tool.** The Python
brain and the web body have completely different toolchains (`uv`/`ruff`/`mypy`
vs `npm`/`eslint`/`tsc`) and no shared code today — the only thing they will
eventually share is an HTTP contract. A workspace manager (Turborepo, Nx, npm
workspaces) would add configuration that buys nothing until there is a second
JavaScript package. Two independent CI jobs in one workflow gives the same
guarantee with no new dependency. Revisit if a second JS package ever appears.

**Why no Tailwind, no Framer Motion, no Three.js.** The brief's §5 technology
direction lists Tailwind and Framer Motion as a *likely* direction, to be
validated rather than assumed. Validated here, they lose:

- The core is one composition of ~10 layers with hand-tuned conic gradients,
  radial masks, 3D-tilted orbital planes and staggered keyframes. Tailwind's
  value is consistency across many components; there is one component, and
  every declaration in it is bespoke. CSS Modules give the same scoping with
  zero dependencies and zero build configuration.
- Framer Motion animates React state. Almost nothing here is React state — the
  motion is continuous, infinite and compositor-driven, which is exactly what
  CSS keyframes already are. The one thing CSS cannot do smoothly is change an
  animation's *speed* without restarting its clock, and the platform already
  solves that: `Animation.playbackRate` via the Web Animations API. That is a
  ~40-line hook (`useOrionTempo`), not a 100 kB dependency.
- Three.js would put a WebGL renderer, a scene graph and a render loop between
  ORION and a shape that is, geometrically, a sphere and four rings. It also
  costs battery on the phones the brief requires this to run on.

Total added runtime dependencies: `next`, `react`, `react-dom`. Nothing else.

**Four presentation states, not a second state machine.** The interface renders
IDLE / LISTENING / THINKING / SPEAKING. The backend owns five
(`orion.voice.schemas.VoiceState`, sequenced by
`orion.voice.state.VoiceStateMachine`): UNDERSTANDING and ANALYZING are separate
stages of work but identical to a human watching a screen, so they collapse into
one THINKING presentation. Critically,
`frontend/src/state/orion-visual-state.ts` contains **no transition validation
at all** — no `_ALLOWED_TRANSITIONS` mirror, no graph, no legality checks.
Duplicating that graph in TypeScript would create exactly the second, silently
drifting state machine this design exists to avoid; transition legality stays
the sole property of the Python machine. When Phase 2+ connects a real runtime,
the only new code required is one total function at the integration boundary
mapping `VoiceState -> OrionVisualState`. It is deliberately not written yet:
there is no runtime to receive states from, and an unused adapter is a
liability, not a head start.

**Honesty about audio.** Mission 10's brief says SPEAKING must react to ORION's
*actual* voice audio, which is Phase 4 work. This prototype therefore does not
simulate it. The circular waveform's opening is driven by a single value,
`--envelope`, fed from `OrionCore`'s `amplitude` prop; when that prop is unset
it falls back to a **fixed per-state constant**, never a random number. A
random envelope would be visually indistinguishable from a real one, which is
precisely the kind of fabricated output the brief's quality rules forbid — and
it would be an outright lie in a screen recording. Phase 4 drives `amplitude`
from a real `AnalyserNode` and no markup or stylesheet changes. Randomness is
additionally banned from `frontend/src` by an automated test, partly for this
reason and partly because randomness in render would desynchronise server and
client hydration.

**Security posture.** Phase 1 has no attack surface by construction, and that is
asserted rather than merely claimed in prose:
`frontend/tests/source-safety.test.ts` scans every `.ts`/`.tsx` file under
`src/` (comments stripped) and fails the build on `dangerouslySetInnerHTML`,
`innerHTML`/`outerHTML`/`insertAdjacentHTML`,
`fetch`/`XMLHttpRequest`/`WebSocket`/`EventSource`/`sendBeacon`,
`localStorage`/`sessionStorage`/`indexedDB`/`document.cookie`, `process.env`,
anything matching `api[_-]?key` or `Authorization`,
`getUserMedia`/`AudioContext`/`MediaRecorder`, the two randomness sources, and
`eval`/`new Function`. This is the same committed-guarantee discipline as the
Python side's import-hygiene tests: a rule that survives future commits, not an
observation that today's code happens to be clean. All React rendering is
through JSX text interpolation, which escapes by default.

**Testing scope.** Vitest + jsdom + Testing Library — four dev dependencies, no
Jest, no Playwright, no snapshot infrastructure. 34 tests covering: the four
states exist and are mutually distinguishable; every state renders and tags the
DOM correctly; state profiles reach CSS as custom properties; the amplitude
seam clamps to 0..1; every core layer is present; dev controls and the
microphone affordance drive state; the readout announces politely; no table,
canvas, form, price or ticker appears anywhere in the rendered output; the
layout data is deterministic; and the responsive/reduced-motion structures
exist in the stylesheets. End-to-end browser testing is deferred until there is
a backend to be end-to-end *with*.

**Known limitations, deferred by design:** no backend connection, no
microphone, no audio analysis, no real transcript, no dynamic-information
system (the brief's "transform around the relevant analysis" is a later phase);
the state controls are development affordances, not product UI; the visual
prototype was verified by production build, SSR output inspection, a live
`next start` HTTP fetch and jsdom tests, but **not by an automated screenshot
in a real browser** — browser automation was unavailable in this session, so
final visual judgement (glow balance, motion timing, phone rendering) is the
founder's to make on real devices; typography is a system stack rather than a
webfont, to avoid a build-time network fetch for a prototype whose defining
property is that it needs nothing behind it.

**Files added:** `frontend/` (package.json, package-lock.json, tsconfig.json,
next.config.ts, eslint.config.mjs, vitest.config.mts, .gitignore,
`src/app/{layout.tsx,page.tsx,globals.css}`,
`src/components/{OrionCore.tsx,OrionCore.module.css,OrionInterface.tsx,OrionInterface.module.css}`,
`src/hooks/use-orion-tempo.ts`, `src/lib/core-geometry.ts`,
`src/state/orion-visual-state.ts`, and four test files under `tests/`).
**Files modified:** `.gitignore` (Node/Next artefacts),
`.github/workflows/ci.yml` (a second, independent `frontend` job — the Python
job is untouched), `README.md`. **No Mission 0–9 source touched:** nothing
under `src/orion/` or `tests/` was added, modified or deleted, and
`pyproject.toml`/`uv.lock` are unchanged.

**Verification:** Python baseline re-run after the change — `pytest` 923 passed,
`ruff check .`, `ruff format --check .` and `mypy . --strict` all clean, i.e.
identical to the pre-Mission-10 baseline. Frontend — `npm run lint` (14 files,
0 problems), `npm run typecheck` clean, `npm test` 34 passed, `npm run build`
compiles and prerenders `/` as static content; the served page was fetched over
HTTP and confirmed to contain all 26 particles, all 44 waveform bars, the
lattice, the rings, the sphere and the polite live region, with no error
markers in the SSR output.

---

## 2026-08-27 — Mission 10 Phase 2: the voice interface adapter

**Decision:** Expose the Mission 9 voice runtime over HTTP from a new
`src/orion/interface/` package (FastAPI + Uvicorn, four files), bound to
loopback only, with no authentication, no streaming, no persistence and no
connection to Missions 0–8. Three endpoints: `GET /health`,
`POST /v1/voice/turn` (raw audio bytes), `POST /v1/voice/turn/text` (text
fallback).

**Why FastAPI.** The requirement is "the smallest safe HTTP boundary", and
the deciding factor was not speed or popularity but that this codebase is
already Pydantic-shaped end to end — every schema from `MarketSnapshot` to
`Transcript` is a frozen, `extra="forbid"` Pydantic model. FastAPI validates
request bodies with the same library, so `TextTurnRequest` is a real schema
rather than hand-written `dict` parsing, and a malformed body is rejected
before any handler code runs. It is also plain ASGI underneath, which is
what made the request-size ceiling implementable as ~30 lines of middleware
that wraps `receive` directly rather than as a framework plugin. Flask was
rejected (WSGI, no async, no Pydantic integration, and the blocking voice
turn would need a different concurrency story); Starlette alone was rejected
(it would mean re-implementing request-model validation that FastAPI already
does with the exact library the rest of ORION uses); a bare `http.server` was
rejected as writing a web server by hand to avoid a dependency the brief
already approved. Two runtime dependencies added — `fastapi`, `uvicorn` —
and nothing else. `python-multipart` was specifically **not** added: the
audio endpoint takes raw bytes with an explicit `Content-Type`, so there is
no multipart parser standing between the socket and the size check.

**An adapter, not the orchestrator.** `orion.interface` may import
`orion.voice` and `orion.config`, and nothing else from ORION. It cannot
import `orion.decision`, `orion.risk`, `orion.paper`, `orion.memory` or any
other Mission 0–8 package — enforced by `tests/interface/
test_import_hygiene.py`, not by convention. This is the load-bearing
boundary of the whole phase: the moment an HTTP handler can reach the
decision engine, a request from a browser is one edit away from producing a
trade proposal. The real orchestrator decides *what ORION does with what it
heard*; this layer only decides *how bytes become a turn and back*. The one
thing between "user said something" and "ORION replies" is still
`StaticResponseProvider`, which by design never reads the transcript.

**Zero Mission 9 changes, by using its own extension mechanism.**
`VoiceRuntime`'s docstring states that swapping a component means
"constructing `VoiceRuntime` with different arguments, never editing this
file." The text-fallback path is exactly that: `PreparedTranscription`, a
new `TranscriptionProvider` in `orion.interface`, returns an
already-validated `Transcript` and ignores the audio it is handed, so
speech-to-text is bypassed without the runtime learning about a second kind
of turn. Not one file under `src/orion/voice/` or `tests/voice/` was
modified. The Mission 9 invariants Phase 2 could plausibly have eroded
(`runtime.py` free of HTTP/framework imports, `httpx` confined to the two
Deepgram files, `sounddevice` confined to the two audio-I/O files) are
re-verified from `tests/interface/` looking *inward*, which keeps the proof
current without editing a Mission 9 test either.

**One VoiceRuntime per request, never shared.** `VoiceRuntime` documents
itself as "a single synchronous session object ... not thread-safe and not
reentrant". Serving concurrent requests from a shared instance would mean
one request's `VoiceStateMachine` being advanced by another; the second
caller would get `VoiceRuntimeBusyError` at best and interleaved state at
worst. So every request constructs its own runtime, its own providers and
its own `httpx.Client` (closed in a `with` block), and drops all of them
when the response is sent. Nothing is cached on the service, at module
scope, or in a registry. Asserted structurally: a test counts constructions
across sixteen concurrent turns and requires sixteen distinct objects, and
another checks that no two concurrent callers receive each other's
transcript. `run_turn` is blocking, so both routes hand it to
`run_in_threadpool` rather than declaring `async def` and stalling the event
loop — which is also what makes that concurrency test meaningful rather than
sequential.

**Request/response, not streaming.** No WebSockets, no SSE, no live state
push. A single request/response turn is the smallest thing that proves
browser → HTTP → adapter → Mission 9 → real Deepgram → safe response, and
it needs no connection lifecycle, no reconnection logic, no backpressure
policy and no second state representation on the wire. Phase 3 can render
IDLE/LISTENING/THINKING/SPEAKING from the request lifecycle it already
controls. `VoiceTurnResponse.state` is read from Mission 9's own
`VoiceStateMachine` after the turn; this package declares no states and
validates no transitions, so there is still exactly one state machine in
the backend.

**Audio: base64 in one JSON response, untranscoded.** Mission 9 pinned
Deepgram to `linear16`/`wav`/24 kHz specifically so playback needs no
decoder, and those exact bytes are returned — `base64.b64encode` is the only
thing done to them, verified byte-for-byte identical in a test. base64 costs
~33% on a payload of roughly 100–200 KB over a loopback connection. The
alternatives were worse: a separate "fetch the audio" endpoint would require
storing audio between requests, which this phase forbids outright and which
would also mean either a second synthesis call or server-side state;
metadata-in-response-headers with an `audio/wav` body avoids the inflation
but breaks once a 2000-character multilingual transcript is percent-encoded
past the ~8 KB header ceiling; transcoding to a smaller format was
explicitly forbidden and would undo Mission 9's decoder-free decision.

**Localhost only, enforced rather than documented.** `run()` hardcodes
`127.0.0.1` and exposes no `host` parameter, so the supported entry point
cannot be widened by passing an argument (a test asserts the parameter does
not exist). **This service has no authentication, no authorization, no rate
limiting and no TLS.** Exposing it beyond loopback — a public bind, a port
forward, a tunnel, a container port mapping — is not supported and would
hand anyone who can reach it unmetered use of a paid Deepgram credential.
No authentication was added because adding a real one now means choosing a
session/token model for a UI that does not exist yet, and a fake one
(a static shared secret) would create a false sense of safety. Loopback is
the security boundary until production hardening exists.

**Request-size ceiling before the runtime.** `_BodySizeLimitMiddleware` is
pure ASGI so it can wrap `receive`: it refuses a declared `Content-Length`
over the limit without reading a byte, and independently counts what
actually arrives, so a request that lies about its length or streams chunked
with no declared length is cut off at the same ceiling. `MAX_REQUEST_BODY_
BYTES` *is* `orion.voice.audio_input.MAX_AUDIO_INPUT_BYTES` rather than a
second hand-picked number — one constant means the HTTP boundary can never
drift looser than what Mission 9 accepts. A test drives the ASGI app
directly with an undeclared chunked body to prove the counter, not just the
header check.

**CORS: an explicit two-origin allowlist.** `http://localhost:3000` and
`http://127.0.0.1:3000`, with `allow_credentials=False`. Never `"*"`. A
wildcard would let any page the founder happens to have open drive ORION's
paid credential, and it is precisely the setting that becomes unsafe the
moment authentication is added — better to never have it than to remember to
remove it. Tests assert both that allowed origins are echoed and that
`http://localhost.evil.example`, `https://localhost:3000` and
`http://localhost:3001` get nothing.

**Errors carry no detail, structurally.** `VoiceInterfaceError` takes a
`VoiceInterfaceFault` and *no message parameter*, so there is no argument
through which an exception string, a provider message, a URL or a request
body could be attached — a test asserts passing a second argument is a
`TypeError`. `FAULT_RESPONSES` is the complete closed mapping from fault to
(status, code, sentence), every sentence a literal written at development
time. This is deliberately coarser than it could be: telling "Deepgram heard
no speech" apart from "Deepgram rejected the request" would mean inspecting
a vendor's error text and reflecting a judgement about it back to the
client, which is the coupling that leaks vendor internals one refactor
later. FastAPI's default 422 body (which echoes the offending `input` and a
`loc` path into the payload) is replaced for the same reason. Mission 9
already guarantees credential-free error messages via `orion.http_errors`;
this layer does not depend on that guarantee — it never forwards the message
at all. Starlette still re-raises unexpected exceptions after sending the
clean 500, so a traceback reaches the *server's* log and never the client.

**Zero persistence, asserted against the filesystem.** No database, no
files, no cache, no session, no cookie. `orion.interface` imports none of
`sqlite3`/`sqlalchemy`/`pickle`/`shelve`/`tempfile` and calls no `open()` —
checked by AST, not by inspection. It also imports **no logging framework**,
so no request body, transcript or audio can be written to a log; uvicorn's
access log records method, path and status only, which is additionally why
no endpoint accepts a query parameter. One test runs two complete turns
inside a temporary working directory and asserts the directory is still
empty afterwards.

**Why Missions 0–8 remain untouched.** Nothing in this phase needed them.
Wiring voice to evidence, decisions, risk or memory is the orchestrator's
job, and doing any part of it here would put pipeline logic behind an
unauthenticated HTTP endpoint. `git diff` for this phase touches no file
under `src/orion/` outside `interface/`, and no file under `tests/` outside
`tests/interface/`; `pyproject.toml`/`uv.lock` changed only to add the two
approved dependencies.

**Explicitly deferred:** live state streaming (WebSockets/SSE), barge-in and
interruption, voice-activity detection, conversation history across turns,
a transcript-aware response provider, the dynamic-information system, the
full ORION orchestrator, any Mission 0–8 wiring, authentication /
authorization / rate limiting / TLS, and any deployment beyond loopback.
Frontend integration is Phase 3 and no frontend file was touched here.

**Files added:** `src/orion/interface/{__init__.py, schemas.py, service.py,
app.py}`, `tests/interface/{__init__.py, conftest.py, test_app.py,
test_service.py, test_security.py, test_import_hygiene.py}`.
**Files modified:** `pyproject.toml`/`uv.lock` (added `fastapi`, `uvicorn`),
`.github/workflows/ci.yml` (one added step — the interface tests already run
under the existing `pytest`, so the new step covers the one thing they
cannot: that `uvicorn` really installs and the documented entry point
resolves), `README.md`. No Mission 0–9 source touched.

**Verification:** `pytest` 1035 passed (923 before this phase, 112 added),
`pytest --collect-only` 1035 collected, `ruff check .`,
`ruff format --check .` and `mypy . --strict` (144 source files) all clean.
The 112 new tests cover both turn paths end to end through the real runtime
and real Deepgram provider code with only the socket mocked; every rejection
path; the size ceiling by both the declared-length and streamed-byte routes;
credential non-leakage across every reachable failure mode; no tracebacks or
Python internals in any body; ten hostile strings (script tags, template
expressions, JNDI, SQL, path traversal, header injection, prompt injection)
round-tripping as inert text from both the client and the vendor side;
filesystem-level zero persistence; the CORS allowlist including near-miss
origins; loopback-only binding; per-request runtime isolation under sixteen
concurrent turns; and the full inbound/outbound import-direction matrix.

---

## 2026-08-27 — Mission 10 Phase 3: the frontend meets the backend

**Decision:** Connect the Phase 1 interface to the Phase 2 adapter over one
typed HTTP call, and make the ORION core's SPEAKING state track the real
playback of the real WAV Deepgram produced. Text is the only input; the
microphone, audio-reactive visualization, streaming and interruption all
remain deferred. Zero new dependencies.

**Text first, because it is the only honest input today.** Mission 9 built
the text-fallback path precisely so the interface could be driven on a
machine with no microphone; Phase 2 exposed it at
`POST /v1/voice/turn/text`. Wiring that first means the full chain —
browser → HTTP → `VoiceTurnService` → `VoiceRuntime` → Deepgram TTS →
`SynthesizedSpeech` → the browser's speakers — is proven end to end before
`getUserMedia` adds a permission prompt, a capture format negotiation and a
second failure surface on top of it. Phase 4 then only has to replace where
the words come from.

**SPEAKING means sound is coming out.** This is the one invariant the phase
exists to establish, and it is enforced structurally rather than by
convention. `playTurnAudio` calls `onStart` only when the element's
`play()` promise resolves, and `useOrionTurn` dispatches `speaking` only
from that callback; the arrival of the HTTP response dispatches `heard`,
which deliberately does not touch `visualState`. THINKING therefore lasts
exactly as long as the network call, and SPEAKING exactly as long as the
audio. There is no `setTimeout` anywhere in the Phase 3 code. A mutation
test confirmed the guarantee is real: moving the `speaking` dispatch to the
moment the response arrived failed the suite with
`expected 'speaking' to be 'thinking'`, and the file was then restored
byte-identically (sha256 verified).

**Native `fetch`, native `<audio>`, nothing else.** `axios` would add a
dependency and an interceptor system for a single POST whose only
non-trivial needs — timeout and cancellation — are `AbortController`. An
audio library would add a decoder for a format the platform already plays.
`AudioContext` was rejected for this phase specifically: it buys nothing
until Phase 4 wants analysis, and it costs a user-gesture resume dance and
a second copy of the decoded audio in memory. Dependencies added: **none**.

**The API base is a constant, not an environment variable.** Phase 2's
`run()` hardcodes `127.0.0.1` and defaults to port 8000 and exposes no host
parameter, so there is nothing genuinely configurable to expose. A
`NEXT_PUBLIC_*` variable would create a permanently client-visible
configuration surface and would force `process.env` to be permitted in
frontend source, weakening a source-safety rule that currently holds with
no exceptions. `127.0.0.1` rather than `localhost` is deliberate: on
IPv6-first systems `localhost` can resolve to `::1`, which the IPv4-bound
backend is not listening on.

**No CORS change was needed, and that was verified rather than assumed.**
Phase 2 already allows `http://localhost:3000` and `http://127.0.0.1:3000`,
which is exactly where `next dev` and `next start` serve. A live preflight
from `http://localhost:3000` to `127.0.0.1:8000` returned
`access-control-allow-origin: http://localhost:3000` with no
`allow-credentials`, and a POST from `http://evil.example` received no
grant header at all. **`src/orion/interface/` was not modified in this
phase.**

**The response is not trusted because it is ours.** `parseTurn` validates
all seven fields by hand — type, emptiness, integer-ness, a browser-memory
ceiling on `audio_bytes`, and a consistency check that the base64 length is
exactly `4·ceil(bytes/3)` — and returns a camelCase object, so a value of
type `OrionTurn` cannot exist without having been validated. A schema
library was not added: Zod is ~14 kB of runtime for seven fields, and this
validator is the only thing standing between a malformed response and the
React tree, so being readable in one sitting matters more than being
generic.

**Audio is validated before it reaches the platform.** `decodeTurnAudio`
checks the format against an allowlist, the media type against the labels
that format may legitimately carry, the base64 alphabet and length, the
decoded length against the server's own stated length, and the RIFF/WAVE
magic — before any `Blob` exists. Crucially, **no server-controlled string
ever becomes a MIME type or a URL**: `audio_media_type` is compared against
the allowlist and then discarded, the `Blob` is built with a constant
declared in `orion-audio.ts`, and the only URL used is the `blob:` URL the
platform mints. The object URL is revoked in a `finally` on every path —
success, media error, autoplay block, cancellation — and no URL is minted
at all when validation fails.

**Errors are frontend-authored.** Phase 2's `detail` strings are calm and
credential-free by construction, and the frontend still never renders them.
It classifies the *status code* into one of seven failures and shows a
sentence written in `orion-api.ts`. That way nothing between the page and
ORION — a proxy error page, a captive portal, a future gateway — can put
foreign text on ORION's face, and the interface owns its own voice.
`OrionApiError`/`OrionAudioError` take a failure key and no message
parameter, so there is no argument through which an exception's text could
be attached. Every failure ends at IDLE with the input immediately usable
again, and the caret is handed back automatically.

**Zero persistence, still.** No storage, no cookies, no cache, no file, no
logging — `console` is banned outright in frontend source so a transcript
can never reach a browser console that extensions and screen recordings can
read. The command input sets `autoComplete="off"`, which closes the one
remaining accidental-persistence vector: the browser's own form history.

**Two Phase 1 rules were narrowed rather than dropped.** Phase 1's
source-safety test banned `fetch` and audio APIs outright, which was correct
for a frontend that talked to nothing. Phase 3 needs one of each, so the ban
became a per-file allowlist — `fetch` only in `orion-api.ts`, media
playback only in `orion-audio.ts` — plus an inverse assertion that those
files really do use them. That is stronger than deleting the rules: a
component that starts calling the backend directly still fails the build. It
mirrors the Python side's `_ALLOWED_EXTRA_IMPORTS_BY_FILE` exactly. New bans
were added at the same time for `AudioContext`, `AnalyserNode`,
`createMediaElementSource`, `console.*`, `caches`, `document.write` and file
pickers, so "no audio-reactive visualization yet" is a property of the source
rather than a promise. Two Phase 1 *assertions* also had to change because
they encoded facts Phase 3 deliberately made false — the disclosure line no
longer claims "no network", and the "zero inputs, zero forms" check became
"exactly one input, exactly one form, and still no list, table, canvas or
timestamp", which keeps it doing the job it was written for.

**Phase 1's visual design is intact.** `OrionCore.tsx`, its stylesheet,
`orion-visual-state.ts`, `core-geometry.ts` and `use-orion-tempo.ts` are
byte-identical. The command line reuses the hairline/blurred-glass language
the development controls already had, the microphone sits beside it as the
path Phase 4 will make real, and the last exchange occupies two lines of the
readout that already existed — no chat log, no bubbles, no timestamps, no
sidebar.

**The Phase 4 seam is in place and unused.** `playTurnAudio`'s `onStart`
receives the live `HTMLAudioElement`. Phase 3 ignores the argument; Phase 4
can hand it to `createMediaElementSource` and drive a real `AnalyserNode`
without changing a signature or a caller. `OrionCore`'s `amplitude` prop is
untouched and still falls back to a fixed per-state constant. **Nothing
fabricates amplitude, and `Math.random` remains banned frontend-wide.**

**Live verification status — stated precisely.** There is no
`DEEPGRAM_API_KEY` and no `.env` on this machine, so **a successful
vendor-backed turn was never observed and is not claimed**. What *was*
verified live, against the real backend on `127.0.0.1:8000` and the real
production frontend on `:3000`: the page serves (HTTP 200) with the command
surface in the markup; the CORS preflight succeeds for the page's origin and
is refused for a hostile one; a POST with no credential returns a clean 503
`voice_unavailable` with CORS headers; and a POST with a deliberately wrong
credential performs a **genuine Deepgram round trip**, receives 401, and
returns a clean 502 `synthesis_failed` carrying no credential, no vendor
name and no traceback. Successful decode-and-play was proven only against a
locally generated, genuine RIFF/WAVE file through the real module code.
**Browser automation was unavailable this session, so no page was driven in
a real browser**: desktop/mobile layout, actual audible playback and the
visual state transitions on screen remain unverified by eye and are the
founder's to confirm.

**Explicitly deferred:** microphone capture (`getUserMedia`/`MediaRecorder`),
audio-reactive visualization (`AudioContext`/`AnalyserNode`), streaming
(WebSocket/SSE), interruption and barge-in, conversation history, the
dynamic-information system, any Mission 0–8 orchestration, authentication,
and any deployment beyond loopback.

**Files added:** `frontend/src/lib/{orion-api.ts, orion-audio.ts}`,
`frontend/src/hooks/use-orion-turn.ts`,
`frontend/tests/{orion-api.test.ts, orion-audio.test.ts,
orion-turn.test.tsx}`, `frontend/tests/support/audio-harness.ts`.
**Files modified:** `frontend/src/components/OrionInterface.tsx` and its
stylesheet (the command surface), `frontend/tests/orion-interface.test.tsx`
(two assertions that encoded now-false facts),
`frontend/tests/source-safety.test.ts` (narrowed bans plus new ones),
`README.md`. **No Python source, no Phase 2 file and no Phase 1 visual
module was touched.**

**Verification:** frontend — 162 tests passing across 7 files (34 before
this phase, 128 added), `npm run typecheck`, `npm run lint` and a clean
`npm run build` all green. Backend — `pytest` 1035 passed,
`pytest --collect-only` 1035, `pytest tests/interface` 112,
`ruff check .`, `ruff format --check .` and `mypy . --strict` all clean and
identical to the pre-Phase-3 baseline. An independent adversarial probe
sharing no code with the test suite (its own WAV, base64, DOM and fetch
stub) ran 29 checks with zero failures, including 13 hostile audio payloads
and 16 malformed response shapes all refused, `__proto__`/`constructor`/
`toString`/`valueOf` rejected as format keys, no prototype pollution, object
URLs revoked on every outcome, and no credential, vendor name or server path
in any user-visible string. The compiled client bundle was searched and
contains exactly one origin — `http://127.0.0.1:8000` — and zero
occurrences of `deepgram`, `anthropic`, `api_key`, `Authorization`,
`Bearer`, `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie`,
`getUserMedia`, `MediaRecorder`, `AnalyserNode` or `AudioContext`.

## 2026-08-28 — Mission 10 Phase 4: the microphone, and a core that reacts to real sound

**The seam declared in Phase 1 is finally driven, and it did not have to
move.** `OrionCore`'s `amplitude` prop and `playTurnAudio`'s `onStart(element)`
callback were written three phases ago with this in mind, and Phase 4 connects
both without changing either signature. That is the whole return on declaring a
seam early: the diff is additive.

**Four capabilities, four boundary files, four allowlist entries.**
`getUserMedia`/`MediaRecorder` live only in `lib/orion-microphone.ts`;
`AudioContext`/`AnalyserNode` only in `lib/orion-analyser.ts`; `fetch` still
only in `orion-api.ts`; media playback still only in `orion-audio.ts`.
`tests/source-safety.test.ts` enforces each ban everywhere else *and* asserts
the carve-out file really uses it — the same shape as the Python side's
`_ALLOWED_EXTRA_IMPORTS_BY_FILE`. New bans were added rather than only
narrowed: no camera (`video: false` is asserted, and `video: true`,
`getDisplayMedia` and `enumerateDevices` are forbidden outright), no
frequency-domain analysis, no `createScriptProcessor`/`audioWorklet`, no
`FileReader`, no `srcObject`, and no `createObjectURL` outside the playback
boundary — which is what makes "a recording can never be given a URL, written
to disk or replayed" a property of the source rather than a promise.

**RMS, not an FFT — and the brief asked for exactly that.**
`lib/audio-envelope.ts` is pure arithmetic with no browser dependency at all:
root-mean-square over a block of time-domain samples, mapped through a
-62..-12 dBFS window, smoothed by an asymmetric one-pole filter (45 ms attack,
260 ms release) whose coefficient is derived from real elapsed milliseconds.
Logarithmic because speech sits near -30 dBFS and a linear mapping would barely
move the bars; asymmetric because one time constant either lags the start of a
word or strobes on every glottal pulse; frame-rate independent because a
dropped frame must not change the shape of the envelope. Because it touches
nothing, its 24 tests assert exact numbers against synthetic audio — a
full-scale sine's RMS is 1/sqrt(2), one time constant covers exactly 1 - 1/e of
the remaining distance, and two 16 ms steps equal one 32 ms step to twelve
decimal places.

**The two Web Audio graphs have different shapes for different reasons.** The
microphone chain ends in a *zero* gain node wired to the destination: a graph
with no sink is not reliably pulled by every engine, and a live microphone
connected to the speakers at any other gain is an instant feedback howl. The
playback chain *must* reach the destination, because `createMediaElementSource`
permanently reroutes the element and an analyser that did not forward it would
silence ORION. Every step is wrapped: if the graph cannot be built — no Web
Audio, an element already routed, no `getFloatTimeDomainData` — the monitor
returns inert, closes the context it opened, and the core falls back to its
fixed per-state constant. **A failure to visualise sound can never break the
turn producing it.**

**Still four visual states, still one state machine.** No state was added.
Phase 4 simply *reaches* `listening`, which Phase 1 declared and Phase 3 left
to the development controls. The reducer in `use-orion-turn.ts` gained two
actions (`arming`, `listening`) and the amplitude lives in its own `useState`
outside that reducer — the lifecycle and the loudness are different things, and
threading a 60 Hz signal through the lifecycle reducer would blur them. The
backend's `VoiceStateMachine` remains the only authority on the protocol.

**`arming` exists because honesty costs a state, not a lie.** While the
permission prompt is open the interface is locked but the core stays at rest,
because ORION is not listening yet. Claiming LISTENING during the prompt would
have been one line shorter and false.

**The recording ceiling lives in the boundary, not in the caller.**
`MAX_RECORDING_SECONDS = 30` matches
`orion.voice.audio_input.MAX_RECORDING_SECONDS`, and `MAX_RECORDING_BYTES`
matches its `MAX_AUDIO_INPUT_BYTES` (which is also the interface's
`MAX_REQUEST_BODY_BYTES`). The values are restated rather than imported — this
is a different runtime with no access to the Python constants — and the timer
that enforces the ceiling stops the recorder *itself*, so a caller that forgets
to stop cannot leave a microphone open. Every exit path releases every track in
a `finally`: normal stop, ceiling, recorder error, empty result, oversized
result, cancellation, and unmount during the permission prompt.

**The spoken turn reuses the Phase 2 endpoint unchanged.** `requestAudioTurn`
posts the recording as raw bytes to `POST /v1/voice/turn`, which has existed
since Phase 2 and already accepts `audio/webm`, `audio/ogg` and `audio/mp4`.
**No backend file was touched.** The `Content-Type` can only ever be a literal
from `orion-api.ts`: `MediaRecorder` labels its own output
`audio/webm;codecs=opus`, and that string is discarded — the `Blob` is rebuilt
with one of three constants and the header value is checked against a closed
allowlist before the request is made. Empty and oversized recordings are
refused before a byte goes on the wire. The shared request path (timeout,
abort forwarding, `credentials: "omit"`, never reading an error body, response
validation) was extracted rather than copied, because two copies of a security
property is one copy that will eventually be edited alone.

**One performance decision worth recording.** The four generated layers in
`OrionCore` (26 particles, 44 bars, 4 rings, 7 lattice nodes and their chords)
are now wrapped in `useMemo` with empty dependencies. Their markup is
byte-for-byte Phase 1's; the memo exists so an envelope update at 60 Hz
re-renders one element's inline style instead of reconciling ~120 nodes. The
envelope is also quantised to two decimals so React bails out entirely on
frames where nothing perceptible changed.

**Three assertions changed because they encoded facts Phase 4 made false**, and
each was replaced rather than deleted: the microphone button no longer claims
"no microphone is connected", the disclosure line no longer says "no microphone
yet", and Phase 3's "asks for no microphone even when the mic affordance is
used" became "asks for no microphone on a *typed* turn" — which is the property
that file was written to protect and which still holds from keystroke to
silence. No existing test was weakened to let the implementation pass.

**Live verification status — stated precisely.** There is still no
`DEEPGRAM_API_KEY` and no `.env` on this machine, so **a successful
vendor-backed spoken turn was never observed and is not claimed**. Browser
automation was unavailable this session, so **no real microphone was opened, no
permission prompt was seen, and no waveform was watched moving on a screen**;
the entire microphone and Web Audio path is proven against mocks that model the
platform's contract, not against a browser. Desktop/mobile layout, audible
playback and the visual reaction to a real voice remain the founder's to
confirm. What *is* proven here: the envelope arithmetic against synthetic
audio, the device lifecycle and its release on every failure path, the
permission-denial and recording-error paths, the analyser's graph shape and its
teardown, and that hostile transcript text arriving from a *spoken* turn still
reaches no HTML sink.

**Explicitly still deferred:** streaming (WebSocket/SSE), interruption and
barge-in, voice-activity detection and silence-triggered auto-stop,
conversation history, the dynamic-information system, any Mission 0-8
orchestration, authentication, and any deployment beyond loopback.

**Files added:** `frontend/src/lib/{audio-envelope.ts, orion-analyser.ts,
orion-microphone.ts}`, `frontend/tests/{audio-envelope.test.ts,
orion-analyser.test.ts, orion-microphone.test.ts, orion-voice-turn.test.tsx}`,
`frontend/tests/support/media-harness.ts`. **Files modified:**
`frontend/src/lib/orion-api.ts` (the audio turn plus the shared request path),
`frontend/src/hooks/use-orion-turn.ts`, `frontend/src/components/OrionCore.tsx`
(memoised layers, same markup), `frontend/src/components/OrionInterface.tsx`
and its stylesheet, `frontend/src/app/layout.tsx` and `frontend/package.json`
(stale Phase 1 descriptions), the `source-safety`, `orion-api`, `orion-core`,
`orion-interface` and `orion-turn` test files, and `README.md`. **No Python
source and no Phase 2 backend file was touched.**

**Verification:** frontend — 278 tests passing across 11 files (162 before this
phase, 116 added), `npm run typecheck`, `npm run lint` and a clean
`npm run build` all green. The compiled client bundle was searched and contains
exactly one ORION origin — `http://127.0.0.1:8000` — and zero occurrences of
`deepgram`, `anthropic`, `api_key`, `Authorization`, `Bearer`, `localStorage`,
`sessionStorage`, `indexedDB`, `document.cookie`, `getDisplayMedia`,
`enumerateDevices`, `createScriptProcessor`, `getByteFrequencyData` or
`srcObject`. (`FileReader` appears three times, all inside Next.js's own
bundled `whatwg-fetch` polyfill; it appears nowhere in `src/`, which is what
the source-safety rule scans.) Backend — `pytest` 1035 passed, `ruff check .`,
`ruff format --check .` and `mypy src` all clean and identical to the
pre-Phase-4 baseline.

## 2026-08-28 — Mission 10 Phase 5.1: the orchestrator core, at last

**M2-Q9 is answered.** That entry approved shipping *no* orchestrator and no
`pipeline.py`, on the grounds that building one is its own mission rather
than a side effect of another. Twenty-seven references across this document
have pointed here since: `decide()` and `review()` take a caller-supplied
`analysis_id`/`now`/`policy`/`portfolio` precisely because "no orchestrator
exists yet to mint them," and Mission 9's `ResponseProvider` was built as an
empty seam for exactly this. `src/orion/orchestrator/` is that caller. It is
Phase 5.1 of a five-part Mission 10 Phase 5, split because one commit
containing an unproven orchestrator *and* a rewritten frontend is a commit
nobody can review.

**A new package, not a new function in an old one.** The composition root
imports the engines; the engines must never import it. That direction is the
whole reason this is a package rather than a helper bolted onto
`orion.interface` or `orion.voice`, and it is enforced rather than intended:
`tests/orchestrator/test_import_hygiene.py` AST-parses both halves — nothing
in Missions 0–9 imports `orion.orchestrator`, and `orion.orchestrator`
imports no vendor SDK, transport, framework, persistence library, logger or
id-minting module. Deleting this package must leave Missions 0–9 exactly as
they were, and that is now a checked property.

**It could not have lived in `orion.voice`, and that is not a stylistic
claim.** `tests/voice/test_import_hygiene.py` forbids `orion.voice` from
importing `orion.data`, `orion.analysis`, `orion.decision`, `orion.risk` and
seven more. An analysis-backed `ResponseProvider` is therefore *impossible*
inside that package. `orion.orchestrator.response.AnalysisResponseProvider`
implements Mission 9's ABC from outside it, and a dedicated test asserts the
only `orion.voice` modules this package may touch are `response` (the ABC)
and `schemas` (the `Transcript` that ABC is handed) — not the runtime, not
the state machine, not the Deepgram providers, not audio I/O.

**It composes; it computes nothing.** Every step is an existing function
called with existing arguments: `fetch_ohlcv` → `validate_snapshot` →
`build_evidence` → `ReasoningProvider.reason` → `decide` → `review`. No
indicator maths, no rule, no control, no threshold and no second opinion
about anything an engine already decides. `orion.backtest.engine.run_backtest`
is the precedent followed deliberately closely — same chain, same fail-closed
posture, same refusal to mint an identifier from `uuid4()` — differing only
in running once against live data instead of repeatedly against history, and
in stopping at the verdict instead of executing it. `analysis_id` is derived
as `analysis-{symbol}-{timeframe}-{evidence.as_of}`, so re-asking the same
question before a new bar closes yields the same identifier.

**The intent parser is deterministic, and that is a security decision before
it is an engineering one.** `orion.orchestrator.intent` is a closed-vocabulary
token matcher: no LLM, no regex over user-supplied patterns, no network, no
clock, no randomness. `transcript.text` is untrusted external content, and
asking a language model "what did they want?" would put that text inside a
prompt — the exact surface `StaticResponseProvider` was written to avoid
having. Nothing the user typed ever leaves the module: `Intent.symbol` is a
value *looked up in* the alias table and returned from it, never a substring
of the input, so the string that eventually reaches a vendor URL can only be
one this codebase chose. A test asserts the resolved symbol is not a slice of
the utterance, and fourteen hostile inputs (prompt injection, shell
metacharacters, JNDI, RTL override, CJK, emoji, 100k characters) are proven
to raise nothing and leak nothing.

**XAUUSD only, and unrecognised-but-plausible requests are refused.**
`orion.data.twelvedata_provider._SYMBOL_MAP` knows exactly one symbol and
Missions 0–1 are XAUUSD-only, so listing aliases for instruments the data
layer cannot fetch would produce an `Intent` guaranteed to fail one call
later — a worse failure than not recognising the request, because it looks
like it worked. "analyze bitcoin" is therefore `UNKNOWN`, not a resolved
intent. **Timeframe is not parsed at all**: `DEFAULT_TIMEFRAME` (H1) is used
for every analysis and "on the daily" in the utterance does not change it,
with a test saying so explicitly. Which chart ORION analyses is a policy
choice about what ORION *is*, not a per-utterance option, and making it
user-steerable is a product decision that has not been taken.

**Fail-closed, never fail-soft.** `MarketDataProviderError`,
`ReasoningProviderError`, `DecisionEngineError` and `RiskGuardianError` are
never caught in `run_analysis`; they propagate and abort. Market data that
fails `validate_snapshot` raises `AnalysisDataQualityError` rather than being
analysed anyway with a caveat attached — an analysis that silently dropped a
stage would produce a report that *looks* complete while missing the stage
that mattered. The staleness threshold is read from `RiskPolicy.max_staleness_bars`
rather than chosen a second time, so this layer and
`orion.risk.controls.evidence_is_fresh` can never disagree about what "stale"
means. **A known and accepted consequence:** on a weekend, or during a vendor
outage, gold data is genuinely stale and every analysis will refuse. That is
correct — the Risk Guardian would veto regardless — and softening it belongs
to the HTTP layer that owns the fault vocabulary, not here.

**Risk is the one place absence is handled softly, and only because absence
is reportable there.** A missing or unreadable paper account is not an
analysis failure: structure, momentum, reasoning and the proposal are all
still real. So it becomes an explicit `RiskAvailability` reason
(`NO_PORTFOLIO_STORE` / `PORTFOLIO_UNAVAILABLE` / `PORTFOLIO_SYMBOL_MISMATCH`)
and the guardian is simply not consulted. **What never happens is inventing an
account.** A fabricated flat portfolio at the configured starting balance
would make the position-size, exposure, daily/weekly-loss and drawdown
controls evaluate a fiction while presenting as authoritative — precisely
what `orion.paper.store.PortfolioStoreError`'s own docstring forbids papering
over. `RiskPanel`'s validator makes `verdict is not None` if and only if
availability is `AVAILABLE`, so the fabricated case is unconstructible rather
than merely undone.

**Marking to market is required, and is not laundering.** `review()`
independently re-verifies that `equity` equals cash plus the open position's
unrealised P&L at `evidence.last_price`, raising `PortfolioIntegrityError`
otherwise, so the loaded state must be marked forward before review — a pure
projection that is never saved. That projection sets `as_of` to now, which
would otherwise hide how old the stored record actually was, so `RiskPanel`
carries `portfolio_as_of` holding the *stored* timestamp. A test asserts the
panel reports the stored value, not the projection's.

**News and macro are NOT_CONNECTED, structurally.** No news provider and no
News Analyst exist anywhere in this codebase — the Mission 7 v1 and Mission 8
v1 entries above both record that deferral and why — and the Macro Analyst
path is deliberately out of Phase 5.1's approved scope. `NotConnectedPanel`
is how both are reported, and it carries **no list, no mapping, no number and
no timestamp**: only a single-member status enum and a fixed sentence chosen
at development time. There is nowhere in the schema to put a fabricated
headline or an invented macro reading, which is a stronger guarantee than a
convention that nobody does. A test asserts the exact field set, so adding one
is a deliberate, visible act. `CapabilityStatus` has one member on purpose —
a two-member enum would imply a `CONNECTED` case this build can reach.

**The report reuses domain models rather than re-declaring them.**
`AnalysisReport` embeds `Evidence`, `ReasoningOutput`, `ProposedDecision` and
`RiskVerdict` whole, exactly as `orion.voice.runtime.VoiceTurnResult` embeds a
full `Transcript` and `SynthesizedSpeech`. A parallel set of flattened panels
restating `trend`/`rsi`/`confidence` would be a second copy of every field in
the system, free to drift the moment either side is edited. Reducing it to a
client-visible allowlist is the HTTP boundary's job when one exists — see
`orion.interface.schemas.VoiceTurnResponse`, which already performs exactly
that reduction for a `VoiceTurnResult`. The brief's seven requested fields map
onto the structure directly: structure and momentum from `evidence`,
confidence and conclusion from `reasoning`/`decision`, risk from `risk`, and
news/macro from their `NotConnectedPanel`s.

**`respond()` still returns `str`; the report is captured, not returned.**
Widening Mission 9's ABC to carry structure would be a Mission 9 change made
for a Mission 10 convenience. Instead the completed `AnalysisReport` is held
on the provider instance and read back through `last_report`. That is safe
because of how `orion.interface.service.VoiceTurnService._run` already works:
it constructs every provider fresh, per request, sharing nothing between
calls. `last_report` is cleared at the *start* of every `respond`, so a failed
or unrecognised turn can never leave the previous turn's analysis sitting
there looking current — tested from both directions.

**No clock anywhere in the package.** `now` is always a parameter, and
`AnalysisResponseProvider` uses `transcript.transcribed_at` — a real,
provenance-stamped timestamp that arrived with the input — so `respond` stays
a pure function of its argument plus the injected service, exactly like every
engine underneath it. An AST test proves no `now()`/`utcnow()`/`today()`/
`time()`/`monotonic()` call exists in any file.

**Zero persistence, zero configuration, zero credentials.** The portfolio
store is only ever `load()`ed; no journal is touched. `FakePortfolioStore.save`
and `.initialize` raise `AssertionError` in the test suite, so a single write
anywhere in the chain fails outright. `orion.config` is on the banned-import
list: reading the environment belongs to the composition root *above* this one
(`orion.interface.app` already plays that role), and keeping it out is what
lets the whole package be constructed and fully tested with no `.env`, no
credential and no network. **No new dependency was added.**

**Failure is never a verdict, and never leaks.** Every way an analysis can
fail becomes a `ResponseProviderError` carrying one fixed message and no
report — never a spoken guess, never a neutral "conditions look mixed", never
a report with empty fields. The cause is chained for a debugger and discarded
for the user, because a `MarketDataProviderError` may name a vendor, a URL, a
status code or a credential. Tests assert that an error containing
`sk-live-...`, an Anthropic URL and a 401 produces a message containing none
of them.

**Nothing is wired up yet, and that is asserted positively.** Phase 5.1 builds
the orchestrator; Phase 5.2 connects it. `orion.interface.service` still
constructs Mission 9's `StaticResponseProvider`, and a hygiene test requires
that file to name `StaticResponseProvider` and *not* name
`AnalysisResponseProvider` — so "the HTTP surface is unchanged" is a checked
fact rather than a claim in a commit message. **No frontend file, no HTTP
file, and no Mission 0–9 source file was touched.**

**Deliberately deferred to later Phase 5 sub-phases, or beyond:** the Macro
Analyst / Finnhub / second Anthropic call (5.1 scope decision); the HTTP
surface and the `analysis` field on `VoiceTurnResponse` (5.2); the frontend
contract (5.3); the cinematic analysis view (5.4); decision-journal
persistence and Mission 5 reflection wiring (5.5, if approved); news and
sentiment data layers; symbols beyond XAUUSD; user-selectable timeframes;
streaming/WebSockets; and authentication. The broken
`[project.scripts] orion = "orion.cli:main"` entrypoint (`orion.cli` does not
exist) was found during the Phase 5 investigation and is **tracked separately,
not fixed here**.

**Files added:** `src/orion/orchestrator/{__init__,schemas,intent,analysis,
response}.py` and `tests/orchestrator/{__init__,conftest,test_intent,
test_schemas,test_analysis,test_response,test_import_hygiene}.py`. **Files
modified:** this document only.

**Verification:** `pytest` 1174 passed (1035 before this phase, 139 added),
`ruff check .`, `ruff format --check .` (159 files), `mypy src` (75 files) and
`mypy tests/orchestrator` (7 files) all clean. The CI interface-entrypoint
smoke check still resolves all three routes, and `import orion.orchestrator`
succeeds with no environment and no credential present. Frontend — 279 tests,
`typecheck`, `lint` all green and byte-identical to the Phase 4 baseline; no
`frontend/` file, `pyproject.toml` or `uv.lock` was touched. Every test double
implements the real ABC (`MarketDataProvider`, `ReasoningProvider`,
`PortfolioStore`) rather than a `unittest.mock` stand-in, and nothing between
`fetch_ohlcv` and the finished report is stubbed: real validation, real
indicators, real structure, the real Decision Engine and the real Risk
Guardian run on every test in the analysis suite.

## 2026-08-28 — Mission 10 Phase 5.5: decision-journal persistence is
deliberately NOT wired

**The question Phase 5.1 left open, answered: no.** That entry deferred
"decision-journal persistence and Mission 5 reflection wiring" to "5.5, *if
approved*". It is not approved. No production `DecisionJournal` writer is
introduced, no `SqliteDecisionJournal` is constructed in any request path, no
`SqlitePortfolioStore` is opened, and Mission 5 reflection stays unwired.
Phase 5.5 changes no source file. This entry is the deliverable.

**The gap is real and is being left open on purpose.** `DecisionJournal` has
no production writer anywhere in `src/` — every `record_*` call in the
repository lives in `tests/memory/` and `tests/reflection/`. Mission 2 built
an append-only journal; Missions 5 and 6 read from one; nothing in the
running system has ever written to one. That is an architectural gap, and
closing it by adding a writer now would cost more than it buys.

**The interface makes a promise to the user that a journal would falsify.**
`OrionInterface.tsx` renders, to the person using it: "Nothing is recorded,
stored or logged." That sentence has been on screen since Phase 4 and was
rewritten in Phase 3 and Phase 4 rather than allowed to go stale. Wiring a
journal into the turn path would make it false while leaving it displayed.
Deleting the sentence instead would be a product decision about what ORION
*is* — a local prototype that forgets — and that decision has not been taken.
Of every argument below this is the one that settles it: the others are about
cost and correctness, this one is about telling the truth to a user.

**It would require crossing four committed, enforced guarantees, not one
convention.** `tests/orchestrator/test_import_hygiene.py` bans `sqlalchemy`,
`sqlite3` and `orion.memory` from the composition root that actually runs an
analysis. `tests/interface/test_import_hygiene.py` bans the same from the
HTTP adapter. In `tests/interface/test_security.py`,
`test_interface_package_opens_no_files_and_imports_no_database` AST-asserts
that no `open()` call and no database import exists in the package, and
`test_a_completed_turn_writes_nothing_to_disk` runs a real text turn and a
real audio turn against an isolated working directory, then asserts
`list(rglob("*")) == []` — zero bytes, checked against the filesystem rather
than claimed. Enabling persistence means editing four tests whose whole
purpose is to fail when someone does exactly that. A guarantee that is
deleted the first time it is inconvenient was never a guarantee.

**Merely constructing the journal creates the file.**
`orion.memory.engine.create_memory_engine` runs `metadata.create_all(engine)`
— a database file and its tables appear at construction, before anything is
recorded. So there is no "wire it but write nothing" middle position on the
filesystem test; the seam is binary, which `orchestrator/wiring.py` already
documents for the identical reason it declines to open a paper account.

**Half the journal has no producer, and a half-filled audit trail is worse
than none.** The write surface is `record_evidence`, `record_reasoning`,
`record_decision`, `record_verdict`, `record_fill` and
`record_closed_position`. Mission 10 stops at the verdict: `run_analysis`
composes `fetch_ohlcv → validate_snapshot → build_evidence → reason → decide
→ review` and returns, executing nothing. There are no `Fill`s and no
`ClosedPosition`s to record, so a wired journal would persist four of six
record types forever. A reader — or Mission 5's `reflect_on_closed_position`,
which walks `ClosedPosition → Fill → RiskVerdict → ProposedDecision` — would
find a store that looks like an audit trail and contains no outcomes. The
same fact makes reflection wiring moot rather than merely deferred: there is
nothing for it to reflect on.

**A conversational surface is the wrong thing to journal.** `/v1/voice/turn`
answers questions. "Analyze gold" is a question, not a trade, and it arrives
over an unauthenticated loopback POST with no rate limit. Journaling every
turn would turn each question into an appended row with no retention policy,
no cleanup path and no authentication in front of it — unbounded disk growth
driven by whoever can reach `127.0.0.1:8000`, holding a durable record of
what the user asked and when. The journal was designed for a trading loop
with real fills, not for a microphone.

**What honest absence looks like today, and why it is already correct.** The
risk panel reports `NO_PORTFOLIO_STORE` rather than inventing a flat account;
`RiskPanel`'s validator makes the fabricated case unconstructible. News and
macro report `NOT_CONNECTED` through a schema with nowhere to put a
fabricated headline. Phase 5.4 renders both states explicitly — `NOT
AVAILABLE` with a sentence naming why, `NOT CONNECTED` with the backend's own
detail — and its tests assert that an unavailable risk panel can never read
as a verdict. The absence of persistence is disclosed by the same mechanism,
in the same voice, and needs no new code to be honest.

**What would have to be true to revisit this.** Not "the interface exists" —
that is now true and changed nothing. Concretely: an execution loop that
produces real `Fill`s and `ClosedPosition`s, so the journal's write surface
has a producer for all six record types; a decision about retention and about
what the interface tells the user instead of "nothing is recorded, stored or
logged"; and an owner for the crash-consistency boundary the Mission 5 entry
already documents as Finding A (account state and journal are separate SQLite
files with no shared transaction). Until all three exist, the correct number
of production journal writers is zero.

**Documentation note.** Phases 5.2 (HTTP analysis surface), 5.3 (frontend
analysis contract) and 5.4 (cinematic analysis view) shipped without entries
in this document; their rationale lives in commits `058c8dc`, `b9bb3b6` and
`7c8c264`. This entry does not backfill them — it records only the Phase 5.5
decision — but a reader moving from Phase 5.1 to here should know the three
phases in between happened and are documented elsewhere.

**Still deferred, unchanged from the Phase 5.1 list:** the Macro Analyst /
Finnhub / second Anthropic call; news and sentiment data layers; symbols
beyond XAUUSD; user-selectable timeframes; streaming/WebSockets; and
authentication. The broken `[project.scripts] orion = "orion.cli:main"`
entrypoint (`src/orion/cli.py` still does not exist) remains tracked
separately and is deliberately not fixed here.

**Files modified:** this document only. No source file, no test, no
dependency manifest, and no frontend file was touched. `pyproject.toml` and
`uv.lock` are unchanged; no dependency was added or removed.

## 2026-08-28 — Mission 11.1: connector contracts, an empty allowlist, and one
MCP server switched off

**Contracts only. No connector, no MCP, no tool-calling.** `src/orion/
connectors/` ships `Capability`, `ConnectorSpec`, `ConnectorSource`,
`ConnectorResult`, `ConnectorError` and a registry whose allowlist is empty.
There is no connector implementation, no client, no transport, no MCP server,
no MCP client and no mechanism by which a model could invoke anything. The
Mission 11 audit found no candidate connector with demonstrated value to
ORION's analysis chain, so the honest deliverable is the shape a future
connector must fit and nothing occupying it.

**The audit's most useful finding was not about ORION's code.** Next.js 16.3.3
vendors `@modelcontextprotocol/sdk` and ships a development MCP server whose
`experimental.mcpServer` flag defaults to **`true`** (`defaultConfig.
experimental` in `next/dist/server/config-shared.js`). With it on, `next dev`
mounts an unauthenticated JSON-RPC endpoint at `/_next/mcp`; Next constructs
the transport with only `{ sessionIdGenerator: undefined }`, so
`enableDnsRebindingProtection` takes the SDK's default of `false` and neither
`allowedHosts` nor `allowedOrigins` is set — no `Origin` check, no `Host`
check. Its nine tools disclose the absolute project path, the dev log file
path, the route table, compilation errors and server-action source filenames.
It is development-only (attached in `hot-reloader-turbopack`/`-webpack`, absent
from `next-server`/`base-server`) and cannot reach ORION's backend,
credentials, engines or risk system. Whether a browser could actually drive it
was **not** tested and is not claimed.

It is switched off anyway, in `frontend/next.config.ts`, and that is the whole
frontend change: **`experimental: { mcpServer: false }`**. The brief's §19 rule
is an *explicit allowlist*, and an MCP server nobody in this project chose,
evaluated or allowlisted is precisely what that rule exists to prevent. A
framework default is not a decision. `tests/connectors/test_import_hygiene.py`
asserts the flag stays false, so re-enabling it is a visible act.

**READ is the only capability that exists.** `Capability` has one member, for
the reason `CapabilityStatus` has one: a two-member enum would imply a `WRITE`
case this build can reach. Because `WRITE` and `EXECUTE` are not members, a
connector that writes or acts is not "forbidden by policy" — it is
unrepresentable, and `Capability("write")` raises. Granting one later must be
an edit to that enum with an entry in this document behind it.

**The allowlist is code, not configuration.** `CONNECTOR_ALLOWLIST` is a module
constant built at import time through `build_allowlist`, which rejects
duplicates. It is deliberately not read from `Settings`, an environment
variable or a file, so enabling a connector cannot be done by editing `.env` on
a deployed machine. The shipped empty tuple goes through the same validator a
real one will. `require_connector` therefore refuses every input, including
every name the brief lists as a possibility (`github`, `browser`, `database`,
`notifications`, ...) — those are candidates, and none was approved.

**Nothing here can reach the Decision Engine or the Risk Guardian, and that is
structural.** The package imports no other ORION package at all — not
`orion.config`, `orion.risk`, `orion.decision`, `orion.paper` or `orion.memory`
— and no network, filesystem, database, subprocess, logging, dynamic-import or
vendor module. An AST hygiene test proves each claim from the source rather
than from `sys.modules`, in the same idiom `tests/risk/` and
`tests/orchestrator/` already use. A separate test proves nothing in Missions
0-10 imports `orion.connectors`, so deleting this package would leave the rest
of ORION byte-identical — which is what makes shipping an empty contract layer
a safe act rather than a commitment.

**Provenance is unconstructible-without.** A `ConnectorResult` requires a
`ConnectorSource` naming the connector and a timezone-aware `retrieved_at`,
mirroring `DataSourceMeta` and `EconomicCalendarSourceMeta`. A naive timestamp
is refused: it cannot be compared to a bar's `as_of` without guessing a zone,
and guessing is what this layer exists to refuse.

**No caller-provided URL, no caller-provided credential — by absence, not by
validation.** `ConnectorSpec`'s fields are exactly `{name, capability,
description}` and the model is `extra="forbid"`, so `url`, `base_url`,
`endpoint`, `token` and `api_key` are refused by name. A future connector must
hardcode its endpoint the way `orion.voice.deepgram_stt` does, **not** read a
base URL from the environment the way `TwelveDataProvider` and
`FinnhubEconomicCalendarProvider` do — `TWELVE_DATA_BASE_URL` and
`FINNHUB_BASE_URL` are free-form env-overridable strings with no scheme check
and no host allowlist, and that pattern is deliberately not inherited here.

**Errors are detail-free and fail closed.** `ConnectorError.__init__` takes no
parameter, so there is no argument position in which a connector name, a URL, a
query string or a vendor's error text could be placed — the same discipline as
`ResponseProviderError` and `OrionApiError`, and the reason `orion.http_errors`
exists at all after a real HIGH-severity key-in-URL leak in this repository.
`require_connector` raises `from None`, so no cause stays reachable through a
traceback. The one error that *does* carry detail is `build_allowlist`'s
duplicate `ValueError`: `specs` is written in this repository by a developer
and validated at import time, so it can only ever name a string typed here and
never reaches a user.

**Where connector output would enter, when one exists.** As validated evidence
upstream of reasoning, the way `MarketSnapshot` and `EconomicCalendarSnapshot`
already do — never as a tool a model may call. Nothing in this package provides
a mechanism for the latter, which is the point: it is what stops a connector
silently becoming a trading authority.

**Deliberately not done here:** no Python MCP SDK was adopted (it would pull
`sse-starlette`, which `tests/interface/test_import_hygiene.py` bans by name);
no dependency was added or removed; no provider implementation was touched; the
broken `[project.scripts] orion = "orion.cli:main"` entrypoint remains tracked
separately. **Recorded as a separate future hardening item, not done in 11.1:**
the four API keys in `orion.config.Settings` are plain `str`, not `SecretStr`.
Nothing currently dumps, logs or reprs `Settings`, and `describe_http_error`
covers the URL-leak path, but a `model_dump()` would expose them in plaintext.
That change touches Mission 0-10 source and belongs to its own reviewed task.

**Mission 11.2 (one read-only connector, only if a concrete need is identified)
and any MCP runtime remain unapproved.**

**Files added:** `src/orion/connectors/{__init__,schemas,errors}.py`,
`tests/connectors/{__init__,conftest,test_schemas,test_registry,
test_import_hygiene}.py`. **Files modified:** `frontend/next.config.ts` (one
setting) and this document.

**Verification:** `pytest` 1389 passed (1220 before, 169 added), `ruff check
.`, `ruff format --check .` (170 files), `mypy src --strict` (79 files) and
`mypy tests/connectors` all clean. Frontend 509 tests, `typecheck`, `lint` and
a clean production build, byte-identical to Phase 5.5 apart from the one
config setting. No Mission 0-10 source file was touched; `pyproject.toml`,
`uv.lock`, `package.json` and `package-lock.json` are unchanged.

## 2026-08-28 — Mission 11.2: investigated, and deliberately deferred

**No connector is implemented, and the empty allowlist stays empty.** Mission
11.2 asked whether a concrete read-only connector would improve ORION enough to
justify building one. The investigation found none, so `CONNECTOR_ALLOWLIST`
remains `()` and `src/orion/connectors/` remains an unused contract. This entry
is the whole deliverable; no source file, test, dependency or frontend file was
touched.

**The decisive finding was that ORION's two declared gaps are not the same kind
of gap.** `orchestrator/analysis.py` tells every user both "no news source is
connected" and "no macro source is connected". Only the first is a missing
capability. `FinnhubEconomicCalendarProvider` (Mission 7 v1) and
`ClaudeMacroAnalystProvider` (Mission 8 v1) are both built, both tested — 142
tests across `tests/research/` and `tests/analysts/` — and simply not
constructed anywhere outside their own packages. "Macro is not connected" is a
*wiring* statement, not a capability statement, and the gate is deliberate:
`tests/orchestrator/test_import_hygiene.py` bans `orion.analysts` and
`orion.research` from the orchestrator precisely so wiring them in is a visible
edit rather than a quiet import.

**Those two are providers, not connectors, and must stay that way.** Routing
either through the Mission 11.1 contract would wrap an abstraction that already
delivers everything the contract promises — a narrow fixed call shape, no
model-chosen endpoint, provenance in the type (`DataSourceMeta`,
`EconomicCalendarSourceMeta`), fail-closed errors and AST-proven import
isolation — changing no behaviour and adding one indirection. If and when they
are switched on, it is through their own Mission 7/8 path, with its own
approval, not through `orion.connectors`.

**News and sentiment are real gaps, and they belong to Missions 7 and 8.** The
brief assigns news, economic calendar, macro data and sentiment to Mission 7
(§13) and the News Analyst to Mission 8 (§14); §19's connector candidates are a
different list entirely — GitHub, browser, documentation, databases,
notifications, development tools — none of which sits in ORION's analysis
chain. Building a news data layer under a Mission 11 connector heading would be
the "ungoverned data-layer expansion disguised as just the analyst" that the
Mission 8 v1 entry already refused. It also needs no connector abstraction to be
done safely: `claude_macro_provider.py` already demonstrates the pattern for
untrusted external text — fenced `<untrusted_external_data>`, never in the
system prompt, size-capped, no tools bound, and a closed output schema with no
field able to encode a direction, a tool call, code or a URL.

**Three candidates were rejected on security grounds alone, and the 11.1
contract already makes each unrepresentable.** Browser/web research requires
arbitrary URL fetching — `ConnectorSpec` has no URL field and is
`extra="forbid"`. Notifications are a WRITE — `Capability` has no `WRITE`
member, so `Capability("write")` raises. GitHub tooling needs a write-capable
credential to serve a developer rather than ORION's output.

**Mission 11.3 (MCP runtime) remains deferred, and is weaker than before.** A
protocol for invoking connectors has nothing to invoke while the allowlist is
empty. The Next.js development MCP server disabled in Mission 11.1 was
re-verified along four independent links: our `experimental.mcpServer: false`
wins the config merge (user config is spread last), blocks the
`__NEXT_EXPERIMENTAL_MCP_SERVER` escalation (which fires only when the value is
`undefined`), and leaves the middleware gate falsy so `/_next/mcp` is never
mounted. Confirmed statically; no dev server was probed.

**Reopening criteria — all four, not any one:**

1. A genuinely new external data requirement that no existing provider covers.
2. Measurable value to ORION's analysis output, stated before implementation.
3. No duplication of an existing narrow provider, and not a Mission 7/8
   responsibility wearing a Mission 11 label.
4. A strictly read-only, provenance-bearing implementation with a hardcoded
   endpoint, no model-selected URL or tool, and no untrusted text reaching a
   prompt without a closed analyst output schema in front of it.

**Verification baseline, unchanged by this entry:** `pytest` 1389 passed, `ruff
check .`, `ruff format --check .` (170 files), `mypy src --strict` (79 files),
and the import-hygiene plus interface-security suites (109 passed) all clean.
Frontend 509 tests, `typecheck`, `lint` and a clean production build.

**Files modified:** this document only.

## 2026-08-28 — Mission 12.1: a turn ceiling, in process memory

**The Mission 12 audit's P0, and only that.** Loopback bounds who may call ORION;
it never bounded how often. One turn fans out to as many as four third-party
calls — Deepgram speech-to-text, Twelve Data bars, Anthropic reasoning, Deepgram
speech synthesis — three of them metered. Until now the only ceiling on that
fan-out was AnyIO's default worker limiter, measured at 40 and belonging to the
framework rather than to ORION, so one local process could hold forty turns in
flight and drive roughly forty paid provider calls per second. `orion.interface.
limits.TurnLimiter` replaces that with an ORION-owned ceiling: at most
`MAX_CONCURRENT_TURNS = 2` turns at once, and at most
`MAX_TURNS_PER_WINDOW = 30` started per `WINDOW_SECONDS = 60`. Nothing else from
the Mission 12 checklist is implemented here.

**Taken immediately before the only call that can reach a vendor, and never
earlier.** In both route handlers the slot wraps exactly the `run_in_threadpool`
call. A request refused for its content type (415), its shape (422) or its size
(413) costs nothing, so it must not spend a turn from the budget either —
otherwise cheap junk could deny service to a real turn while saving no money. The
converse matters more and is the phase's whole point: a refused turn reaches no
provider at all, which is asserted directly against the `respx` call counters.

**Two ceilings, two different refusals, and only one new word.** Saturating
concurrency is exactly what `RUNTIME_BUSY` ("ORION is already handling a turn",
409) already described, so it is reused unchanged. The per-window cap needed one
new member: refusing because thirty turns have already run this minute is not the
same statement, and reusing the busy fault would have had the interface say
something untrue while handling zero turns. `RATE_LIMITED` (429) is that single
addition — a fixed sentence in `FAULT_RESPONSES`, never composed at runtime,
leaving the vocabulary closed and `set(FAULT_RESPONSES) == set(VoiceInterfaceFault)`
still true. The frontend needed no change: `failureForStatus` already maps every
non-502/503 status to `rejected`, which renders the calm sentence "ORION could not
accept that request."

**Process-local, because that is the honest unit here.** No Redis, no database, no
distributed coordination, no per-client keying, no IP map, no session, no header
inspection. On a loopback service with no authentication there is no trustworthy
client identity to key on, and inventing one — hashing a source address, minting a
cookie — would add tracking state to a service whose interface promises the user
that nothing is recorded. Per-process is also the unit that actually bounds vendor
spend, which is what this phase is for. Distributed limiting is deferred until
there is something distributed to limit; today there is one process on one
machine.

**A fixed window, not a sliding one or a token bucket.** Two integers and a float:
it cannot grow, cannot leak, needs no background task, no eviction policy and no
per-client bookkeeping. The known cost is the boundary burst — up to twice the
per-window figure across two adjacent windows — accepted because the concurrency
ceiling still bounds what those turns can do at any instant. `time.monotonic` is
used rather than a wall clock, so an NTP correction stepping time backwards cannot
silently widen the window.

**The limits are constants, and deliberately not configuration.** They are not
read from `Settings`, `.env` or any environment variable. A security ceiling that
can be raised by editing a file on the machine being protected is a default, not a
ceiling. The `TurnLimiter` constructor accepts them only so tests can drive small,
fast values, and `create_app` passes none; a hygiene test AST-asserts the three
module constants are literals rather than computed, and another asserts the module
imports nothing capable of reading configuration. Three further tests prove a
caller cannot widen the ceiling through a request body, a query parameter or a
header.

**Fail closed, including on its own inputs.** A zero or negative ceiling raises at
construction rather than producing a limiter that permits everything. Every
rejection raises `VoiceInterfaceError`, rendered by the existing handlers with no
detail — no remaining count, no window, no reset time, no `X-RateLimit-*` header,
asserted. The slot is released in a `finally`, because a limiter that leaks
capacity under provider failure eventually refuses everything, which is a denial of
service dressed as a safety feature.

**Zero persistence is unchanged, and re-proved with the limiter in the path.**
Three counters on one object owned by one app. Nothing opens a file, touches a
database or writes a byte; `tests/interface/test_security.py`'s filesystem
assertion is re-run in the new suite over a burst that is mostly *refusals*, and
still finds an empty directory. Restarting the process resets the limiter because
there is nowhere for the state to survive. The interface's promise to the user —
"Nothing is recorded, stored or logged" — is untouched, and no logging was added.

**Two Phase 2 tests were re-pointed, not weakened.**
`test_concurrent_requests_do_not_share_a_runtime` and
`test_concurrent_requests_return_their_own_transcripts` drive sixteen overlapping
turns to prove *runtime isolation* — a property about the service, not about how
many turns ORION permits, and one that predates this ceiling by several missions.
They now run on an `isolation_client` fixture whose limiter is sized for the
scenario, so a 409 can only mean the bug they were written to catch. Every
assertion in both is unchanged, and the shipped ceiling is proven separately, and
proven to equal the module constants, in `tests/interface/test_rate_limit.py`.

**Deliberately not in this phase:** authentication, authorization, structured
logging, request IDs, persistence, telemetry, usage/cost tracking, `SecretStr`
hardening, the broken `orion.cli` entrypoint, and every other Mission 12 item. The
deployment model is unchanged: loopback only, and this is a cost ceiling rather
than an access control — it does not make exposing the service beyond loopback
safe, and `app.py`'s docstring now says exactly that instead of claiming there is
no rate limiting at all.

**Files added:** `src/orion/interface/limits.py`,
`tests/interface/test_rate_limit.py`. **Files modified:**
`src/orion/interface/app.py` (import, one constructor parameter, two `with`
blocks, docstring), `src/orion/interface/service.py` (one enum member, one
`FAULT_RESPONSES` row), `tests/interface/conftest.py` (one fixture),
`tests/interface/test_service.py` (two tests re-pointed at it),
`tests/interface/test_import_hygiene.py` (five limiter assertions), and this
document. No dependency was added.

**Verification:** `pytest` 1440 passed (1389 before, 51 added), `ruff check .`,
`ruff format --check .` (172 files), `mypy src --strict` (80 files) all clean.
Frontend 509 tests, `typecheck`, `lint` and a clean production build, byte-identical
to Mission 11.2. Seven mutations — a leaked slot, a decremented window counter, a
window that never resets, each endpoint left unguarded, an environment-readable
ceiling, and the two faults swapped — were each caught by between one and six
tests, and every mutated file was restored byte-identically.

## 2026-08-28 — Mission 12.2: supply-chain and secret scanning, in CI only

**Every quality gate ORION has is about code it wrote; none of them looked at
code it merely trusts.** `ruff`, `mypy`, `pytest` and the frontend gates all
read `src/` and `frontend/src/`. Nothing read `uv.lock` or
`frontend/package-lock.json`, which between them pin 54 Python distributions and
the whole npm tree, and nothing looked for a credential in the history at all —
and this repository handles five provider API keys. This phase adds four checks
over those two blind spots and changes nothing else. No runtime code, no
frontend source, no provider, no risk/decision/paper/memory code; no auth, no
logging, no persistence, no telemetry.

**A separate workflow, so the existing gates could not be touched by accident.**
The scanners live in `.github/workflows/security.yml` and
`.github/workflows/ci.yml` is byte-identical to Mission 12.1. Adding jobs to
`ci.yml` would have meant editing the file that carries ruff, formatting, mypy,
pytest + coverage, the interface entrypoint smoke check and the four frontend
gates; a separate file cannot weaken, reorder or skip any of them. Both files
validate against the published GitHub Actions workflow schema, and
`.github/dependabot.yml` against the Dependabot 2.0 schema.

**Lock files are the source of dependency truth, in both ecosystems.**
`uv export --frozen` forbids re-locking, so pip-audit is handed exactly the set
`uv.lock` pins rather than a set some resolver picked on the runner; `--no-deps`
stops pip-audit resolving its own tree on top, and `--strict` fails the job when
a package could not be audited instead of reporting a quiet all-clear. The
export is written to `$RUNNER_TEMP`, outside the checkout, so no generated
requirements file can end up staged. On the frontend,
`npm audit --audit-level=high` reads `package-lock.json` directly — the job
deliberately does not run `npm ci`, which means the audit sees the locked tree
*and* no package install script ever executes on the runner.

**pip-audit is a CI tool, not an ORION dependency.** It is invoked through
`uv tool run --from pip-audit==2.10.1`, in its own ephemeral environment. Adding
it to `[dependency-groups] dev` would have put a scanner and its transitive
dependencies into `uv.lock` — enlarging the very attack surface being measured,
and making the audited tree different from the shipped one. `pyproject.toml`,
`uv.lock`, `frontend/package.json` and `frontend/package-lock.json` are
unchanged by this phase.

**Gitleaks, pinned twice, and no allowlist.** Secret scanning is
`gitleaks/gitleaks-action` pinned to the commit SHA of v3.0.0, with
`GITLEAKS_VERSION: 8.30.1` pinning the scanner binary as well — left unset the
action installs its own default (8.24.3), so the ruleset, and therefore the
finding set, could change with no diff in this repository. `fetch-depth: 0`,
because a secret that was committed and later deleted is still leaked. The
result that mattered: gitleaks 8.30.1 with its **default** ruleset scans all 29
commits and reports zero findings, including the deliberate fixtures
(`sk-live-supersecret-1234567890` in three provider suites,
`dg-live-supersecret-1234567890` in the two Deepgram suites, and the
`test-anthropic-key` / `test-twelvedata-key` settings fixtures). So no
`.gitleaks.toml` was added. That is the narrowest possible configuration —
there is no allowlist to be wrong about, and no path exclusion that could hide a
real secret under `tests/` later. The fixtures stay exactly as they are: they
exist to prove ORION never leaks a key into an error message, and deleting them
would delete that proof. If a future ruleset ever does flag them, the fix is an
allowlist of those two exact literal strings — never a rule or a path exclusion.

**Artifacts and comments are off, on purpose.**
`GITLEAKS_ENABLE_UPLOAD_ARTIFACT: false`, because on a real leak the SARIF
artifact would be a second, separately downloadable copy of the secret; findings
appear in the job log and the run summary instead. `GITLEAKS_ENABLE_COMMENTS:
false`, because pull-request comments need `pull-requests: write`, and every job
in the workflow holds `contents: read` and nothing more.

**Dependency review runs, but informationally, and for an account reason rather
than a code reason.** `actions/dependency-review-action` (pinned to the v5.0.0
SHA, `fail-on-severity: high` to match the npm audit bar) needs the dependency
review API, which needs the dependency graph enabled — and on a private
repository, GitHub Advanced Security. Without it the action returns 403, which
says nothing about the pull request's dependencies, and blocking every pull
request on an unavailable API is not a security control. The job therefore
carries `continue-on-error: true` with that reason written above it, and the two
lock-file audits are the hard gates. Deleting that one line is the whole change
once the owner enables it.

**No vulnerability was found, so nothing was upgraded.** pip-audit over all 54
locked distributions reports no known vulnerabilities against both the PyPI
advisory service and OSV; `npm audit --audit-level=high` reports 0. Both audits
therefore ship as hard gates from the first run — the "make the first scan
informational" escape hatch was not needed and was not used. Had there been a
finding it would be named here; a dependency bump to turn CI green is a
separate, reviewed change, never a side effect of adding a scanner.

**Dependabot covers the drift a one-off scan cannot.** Three ecosystems: `uv` at
the root (it reads `uv.lock`, unlike `pip`, which would only see the ranges in
`pyproject.toml` and leave the lock behind), `npm` under `/frontend` where the
lock actually lives, and `github-actions` — because a SHA-pinned action is only
an improvement if something keeps it current, and this workflow pins two. Dev
tooling is grouped per ecosystem so the runtime dependencies, the ones worth
reading line by line, arrive as their own pull requests. Nothing merges itself:
every Dependabot pull request runs both workflows first.

**Still owner-side, and deliberately not faked in a committed file:** GitHub
secret scanning and push protection, Dependabot alerts, Dependabot security
updates, and the dependency graph (plus Advanced Security while the repository
is private) are repository/account settings with no file representation. This
phase adds none of them and claims none of them; `.github/dependabot.yml`
governs *version* update pull requests only.

**Deliberately not in this phase:** `SecretStr` hardening, the broken
`orion.cli` entrypoint, and every other Mission 12 item. SHA-pinning the
first-party `actions/*` and `astral-sh/setup-uv` references already in `ci.yml`
was also left alone — that would edit the existing quality workflow, and the
`github-actions` Dependabot entry now maintains those tags.

**Files added:** `.github/workflows/security.yml`, `.github/dependabot.yml`.
**Files modified:** this document only. No application file, no test, and no
dependency manifest was touched, and no dependency was added.

**Verification:** `pytest` 1440 passed (unchanged from Mission 12.1), `ruff
check .`, `ruff format --check .` (172 files), `mypy src --strict` (80 files),
the import-hygiene plus interface security/rate-limit suites (151 passed) and
the interface entrypoint smoke check all clean. Frontend 509 tests, `typecheck`,
`lint` and a clean production build. Both scanners were run locally against this
commit — pip-audit 2.10.1 over the exported lock, against the PyPI and OSV
services, and gitleaks 8.30.1 over the full history, its download checksum
verified against the release manifest — and both pinned action SHAs were
confirmed against their tags.

## 2026-08-29 — Backfill: the six entries this log was missing, and the 12.3 gap

**This entry and the six below it were written retroactively, at `6aa6bc3`.**
Everything from `717ea35` to `6aa6bc3` shipped without an entry here, so for six
consecutive commits the only record of *why* was the commit message. The Mission
10 Phase 5.5 entry already flagged the same drift for Phases 5.2–5.4 and declined
to backfill them; that was the right call for three phases whose rationale was
narrow and whose commits are easy to read end to end. It is the wrong call at
six, because two of them — the CLI entrypoint and the Macro Analyst wiring — are
load-bearing for the Mission 4 Phase 2 work that comes next, and one of them
reverses a promise this document makes about credentials.

**These are backfills and are marked as such.** Each entry below is distilled
from its own commit message and diff, not reconstructed from memory. Where a
commit did not record a number, this document does not invent one: per-commit
test totals are stated only where the commit itself stated them. The verified
totals at `6aa6bc3`, the commit this backfill is written against, are 1653 Python
tests, 541 frontend tests, `ruff`, `mypy --strict`, `eslint`, `typecheck` and a
clean production build.

**Mission 12.3 does not exist and was never deferred.** The sequence runs 12.1,
12.2, 12.4, 12.5, and a gap in a numbered sequence otherwise reads as lost work.
It is not: `12.3` appears in no commit, no branch, no test and nowhere in this
document. No capability was planned under that number, so there is nothing to
defer and nothing to deprecate — the number was simply skipped when Mission 12.4
was named. It is recorded here so a future reader stops looking. If a Mission 12
item is ever needed again, it should take the next free number rather than
backfill 12.3, because reusing a skipped number to mean something new is how a
log stops being a record.

**Files modified:** this document only. No source file, no test, no frontend file
and no dependency manifest was touched by this backfill.

## 2026-08-28 — Mission 12.4: the CLI entrypoint that was declared but never written

*(Backfilled — see the 2026-08-29 note above. Commit `717ea35`.)*

**`pyproject.toml` had advertised a command that could not start.**
`[project.scripts] orion = "orion.cli:main"` has been declared since Mission 0
with no `orion/cli.py` behind it, so the installed `orion` command failed with
`ModuleNotFoundError` before printing anything. Mission 12.2's entry listed the
broken entrypoint as deliberately out of its scope; this is where it was fixed.

**One verb, over the orchestrator that already existed.** `orion analyze
<symbol>` runs exactly the pipeline Mission 10 Phase 5.1 assembled —
`parse_intent` → `build_analysis_service` → `AnalysisService.run` — adding no
stage, reordering none and skipping none. `argparse` and `datetime` are stdlib,
so no dependency was added.

**No `trade` or `execute` verb, on the stated grounds that nothing in the
codebase could place an order.** That reasoning was correct at the time and is
superseded, for paper execution only, by the approved Mission 4 Phase 2 decision;
see that entry when it lands. It was never an argument against *simulated*
execution — it was an argument against a command that looked like it could reach
a broker.

**The symbol is never passed through.** What the user types goes to
`orion.orchestrator.intent.parse_intent`, whose `Intent.symbol` is a value looked
up in the closed alias table rather than a substring of the input, so the string
reaching `MarketDataProvider.fetch_ohlcv` can only ever be a symbol this build
declares. An unrecognised argument is refused at the CLI rather than forwarded to
fail at the vendor one call later.

**Nothing is written to disk.** No portfolio store is constructed, so
`orion.memory.engine.create_memory_engine` — which creates a database file and
its tables as a construction side effect — is never reached. The risk panel
reports `no_portfolio_store` honestly, exactly as it does over HTTP. Every
failure prints one fixed sentence with no exception text, vendor name, path or
traceback, for the same reason `orion.http_errors` exists.

**Files added:** `src/orion/cli.py`, `tests/test_cli.py`. **Files modified:**
`README.md` — the Running section now says what the command needs and that it
never trades, and the project layout no longer lists a `pipeline.py` that has
never existed. 3 files, 754 insertions.

## 2026-08-28 — Mission 10 Phase 6: the emerald cinematic core

*(Backfilled — see the 2026-08-29 note above. Commit `ce2c281`.)*

**Styling only.** The interface was re-themed from cyan/blue to emerald and the
core's sense of life deepened, with no component, hook, state model, geometry or
backend change and no dependency added. The single non-CSS edit is the browser
theme-colour string in `layout.tsx`, matched to the new ground.

**Palette.** A per-state emerald accent (idle, listening, a teal-shifted
thinking, speaking), a near-black green ground, and 76 neutral tints re-cast from
blue at the same luminance, so contrast is unchanged rather than merely
re-checked.

**Everything added to the core rides on pseudo-elements of existing markup** — a
graduated bezel and four cardinal marks so it reads as instrumentation, two
internal convection cells on coprime 19s/29s periods, a specular sweep, a
drifting hotspot, twinkling mote halos, and an uneven ambient haze sweeping the
field every 96 seconds. Coprime periods are the point: two cells that never
present the same arrangement twice inside a minute are the difference between
"alive" and "looping".

**Thinking geometry propagates a signal.** The seven chords carry a wave with a
sharp-attack, slow-decay envelope, the nodes fire rather than breathe, and the
graph counter-wobbles against its own rotation so the figure never repeats.

**Development controls rest at a third of their weight** and come up on hover or
focus, keeping the core dominant. Every animation added here is covered by
`prefers-reduced-motion`.

**Files modified:** `frontend/src/app/globals.css`,
`frontend/src/app/layout.tsx`, `OrionAnalysisDeck.module.css`,
`OrionCore.module.css`, `OrionInterface.module.css`. 5 files, 476 insertions,
95 deletions. No backend file and no dependency manifest was touched.

## 2026-08-28 — Mission 12.5: the four provider credentials become `SecretStr`

*(Backfilled — see the 2026-08-29 note above. Commit `684ed2b`.)*

**This corrects a claim this document had been making.** Earlier entries describe
ORION as careful never to leak a credential through an error path, and the
provider suites carry deliberate `sk-live-…` fixtures to prove it. But the four
credentials on `Settings` were plain `str`, so `repr()`, `str()`, `model_dump()`
and `model_dump_json()` each returned all four in full. `Settings` is built fresh
per analysis and per request, so a single caught traceback frame printed every
key ORION holds. The provider-level guarantee was real; the settings object
underneath it was not.

**Typed as `pydantic.SecretStr`, which masks all four renderings**, and unwrapped
with `get_secret_value()` at exactly three call sites in the two composition
roots — passed as the argument itself, never bound to a local, so no intermediate
name holds a plaintext key in a frame. A token-level test pins the count to those
three sites, which makes a fourth unwrap a test failure rather than a review
miss.

**`analysis_available` keeps testing truthiness rather than materialising the
secret**: an empty `SecretStr` is already falsy, so asking whether a credential is
configured never unwraps one.

**Deliberately unchanged:** environment and `.env` loading, aliases, defaults,
every non-credential field, all provider signatures, and the error vocabulary.
`VoiceTurnService` still takes a `str` on purpose — the narrow boundary is the
audited one, and widening `SecretStr` across an interface that does not need it
would spread the type without spreading the guarantee.

**Files modified:** `src/orion/config.py`, `src/orion/interface/app.py`,
`src/orion/orchestrator/wiring.py`. **Files added:**
`tests/test_secret_handling.py` — 25 regression tests, per the commit: the four
renderings plus a traceback and an f-string, per-field round trips, environment
and `.env` loading, the real key reaching `TwelveDataProvider` and the Anthropic
SDK client, a `ValidationError` that echoes nothing, and the token-level count.
4 files, 301 insertions. No dependency was added.

## 2026-08-28 — Mission 7/8: the Macro Analyst, finally connected

*(Backfilled — see the 2026-08-29 note above. Commit `125be5e`.)*

**Two missions had been built and left unconnected.** Mission 7 built the
economic-calendar data layer and Mission 8 built the Macro Analyst;
`FINNHUB_API_KEY` had no consumer anywhere in `src/`, and every analysis reported
"No macro source is connected in this build". This wires both.

**`MacroPanel` is shaped like `RiskPanel`, and for the same reason.** It reports
four honest states — `not_connected`, `no_events`, `macro_unavailable`,
`available` — so "nothing found", "could not look" and "cannot look at all" stay
distinct, and none of them is answered by inventing macro context. `wiring.py`
builds the calendar provider and the analyst together and only when
`FINNHUB_API_KEY` is present; without it nothing changes and no second vendor is
contacted.

**Advisory only, and structurally so.** Macro runs *after* the decision exists,
and a test compares whole serialised decision, risk, evidence and reasoning
objects across every macro state to prove none of them moves. This is the same
authority boundary Mission 3 established: the Risk Guardian can veto, an analyst
can only annotate. Mission 7's quality gate still runs, so a stale snapshot never
reaches the paid analyst, and neither does an empty batch.

**Fail-soft, unlike every other stage.** A calendar outage returns a complete
analysis with macro marked unavailable rather than destroying the report. Vendor
exceptions are caught broadly and never read — the panel says it could not look,
without saying what the vendor said.

**The model identifier, prompt version, input fingerprint and per-event
assessment ids stop at `_to_macro_summary`.** On the wire `MacroSummary` carries
five values and a count. News is untouched and remains structurally incapable of
claiming connectivity.

**The orchestrator's per-file import allowlist was widened deliberately** — the
note it replaced asked for exactly that — and narrowed to exact modules rather
than whole packages. `orion.interface` still may not import `orion.analysts` or
`orion.research`. This is the wall that Mission 4 Phase 2 must also respect, and
the precedent for how it is widened when it genuinely must be.

**The frontend gained no UI:** the analysis deck's existing Macro row renders a
richer view model, so no component or stylesheet changed.

**Files modified:** `src/orion/orchestrator/{analysis,schemas,wiring}.py`,
`src/orion/interface/{schemas,service}.py`, `frontend/src/lib/{analysis-view,
orion-analysis}.ts`, plus four test files. **Files added:**
`tests/orchestrator/test_macro.py`, `frontend/tests/orion-macro.test.ts`.
14 files, 1140 insertions. No dependency was added.

## 2026-08-28 — Unspoken text turns: ORION answers without a speech credential

*(Backfilled — see the 2026-08-29 note above. Commit `90b7ab1`.)*

**A missing text-to-speech vendor was silently gating the entire product.**
`VoiceRuntime` required a `SpeechSynthesisProvider` and an `AudioOutput`, so
`run_turn()` always ended in the SPEAKING leg, and `VoiceTurnService` gated both
endpoints on `DEEPGRAM_API_KEY` — including the typed one. A deployment with no
speech vendor could not reach analysis, risk or macro at all: `POST
/v1/voice/turn/text` answered `503 voice_unavailable`. ORION's reply is text
first; the audio is a second rendering of it, and the architecture had that
backwards.

**Synthesis and audio output became together-optional.** With neither, a turn
runs `IDLE → LISTENING → UNDERSTANDING → ANALYZING → IDLE` — a path
`orion.voice.state`'s graph has always permitted, because failure recovery needed
an IDLE edge out of every state. The graph, the transition rules and the recovery
guarantee are untouched; this uses an edge that was already there rather than
adding one. Half-wiring — one without the other — is refused at construction.

**Nothing invents silence.** `SynthesizedSpeech.audio` keeps `min_length=1`, so
an unspoken turn reports `speech=None` rather than an empty buffer. On the wire a
new boolean pairs with four now-nullable audio fields under an all-or-nothing
validator, so audio is *absent* rather than empty and a client branches on a
boolean instead of inferring from a length.

**The audio endpoint is unchanged and still refuses without Deepgram** — there is
no way to transcribe speech without it — and a credentialed deployment speaks
exactly as before. `/health`'s `text_fallback_available` is now genuinely
independent of the speech credential instead of being the same boolean under a
second name.

**Files modified:** `src/orion/voice/runtime.py`,
`src/orion/interface/{app,schemas,service}.py`, `frontend/src/lib/{orion-api,
orion-audio}.ts`, `frontend/src/hooks/use-orion-turn.ts`, plus four test files.
**Files added:** `tests/voice/test_unspoken_turn.py`,
`frontend/tests/orion-unspoken-turn.test.ts`. 13 files, 492 insertions. No UI
component or stylesheet changed and no dependency was added.

## 2026-08-29 — Mission 10 Phase 6: spatial composition, and the capability-intent fix

*(Backfilled — see the 2026-08-29 note above. Commit `6aa6bc3`.)*

**A closed-vocabulary parser made a supported question look unsupported.** Every
table in `orion.orchestrator.intent` matches whole tokens, so `"u"` was not
`"you"` and `"what can u do"` fell through to the unrecognised answer — a
question the local response layer was built specifically to answer. The fix is a
closed `_TOKEN_NORMALISATION` dictionary that folds a fixed set of chat spellings
onto the words they stand for *before* any table is consulted: a dictionary
lookup on a whole token, with no regex, no stemming, no edit distance and no
inference. The deterministic, no-model, no-network security model of that module
is unchanged.

**The load-bearing property is asserted, not assumed.** A test proves no value in
the normalisation table is a symbol alias, so folding shorthand onto real words
can never resolve a symbol the raw text could not — `_SYMBOL_ALIASES` remains the
only route to a `symbol`, and therefore to a vendor URL. Analysis still outranks
every other intent in the same sentence, because the analyze branch still runs
first.

**The interface became one composition on one axis.** Two earlier arrangements
failed in opposite directions: a vertical stack made the reply steal height from
the core, and a left/right split fixed that but read as a two-column landing
page. The reply now rises into the outer part of the core's own field, where
there is nothing but hairline orbits and drifting particles — so ORION and what
ORION says are one object rather than two stacked ones.

**The core's size is a function of the viewport and of nothing else.** No
selector keyed on a reply or an analysis resizes it. When a report is taller than
the field, the core stays put and only the report moves. An earlier revision let
the layout halve the core whenever a reply arrived, which inverted the hierarchy
the whole mission is built around.

**Three defects were found by rendering the page, not by reasoning about it.**
`.voice::before` was an absolutely positioned pseudo-element inset *outward*
inside a scroll container — overflow by construction, which gave the field both a
horizontal and a vertical accent-coloured scrollbar across the composition; it is
now a background, which cannot overflow. The absolutely positioned development
corner sat underneath the command line at 1024×768, fixed with one reserved
lane rather than a per-viewport rule. And the thinking-state lattice rotated
about the corner of its own viewBox (`-50 -50 100 100`) rather than its centre,
rendering a few hundred pixels clear of the core; `transform-box: fill-box`
measures against the group's own bounding box instead.

**Verified by driving a real browser, not by arithmetic.** Chrome over the
DevTools Protocol at 1920×1080, 1440×900, 1366×768, 1280×800, 1024×768, 844×844,
844×390 and mobile portrait: zero page scroll on both axes, no nested scroll
regions on any listed viewport, core 422–560px on desktop, and identical geometry
across idle, listening, thinking and speaking. `devIndicators: false` turns off
Next's own dev badge, which is framework tooling that anchored itself over the
application and read as part of the product.

**Files modified:** `src/orion/orchestrator/{intent,response,schemas}.py`,
`src/orion/interface/service.py`, `frontend/next.config.ts`,
`frontend/src/components/{OrionInterface.tsx,OrionInterface.module.css,
OrionCore.module.css,OrionAnalysisDeck.module.css}`,
`frontend/src/hooks/use-orion-turn.ts`, plus three test files. **Files added:**
`tests/orchestrator/test_local_response.py`. 14 files, 1452 insertions. No
dependency was added.

**A note on scope, recorded because it is unusual.** Five of the Python files in
that commit — `interface/service.py`, `orchestrator/{response,schemas}.py` and
two interface test files — were already modified in the working tree before the
session that produced it, and carry the local-response layer
(`IntentKind.GREETING`/`CAPABILITIES`, `LocalResponseProvider`, and its service
wiring). They were committed together because they are prerequisites, not
companions: `intent.py` returns `IntentKind.CAPABILITIES`, which that `schemas.py`
defines, and `test_local_response.py` imports `LocalResponseProvider`, which that
`response.py` defines. Splitting them would have produced a commit that does not
build.

## 2026-08-29 — Mission 4 Phase 2a: a real paper account, opened only when asked

**The Risk Guardian has never run its portfolio controls in a running system.**
Mission 3 gave it seven of them — position sizing, exposure, daily and weekly
loss limits, drawdown against the equity high-water mark, conflicting positions,
portfolio staleness — and Mission 4 built the account they read. But
`build_analysis_service`'s `portfolio_store` parameter has defaulted to `None`
since Mission 10 Phase 5.1, and nothing in `src/` ever passed one, so every
analysis over HTTP and every analysis from the CLI reported
`no_portfolio_store` and those seven controls executed only in tests and
backtests. `orion.paper` had exactly one consumer in the whole of `src/`:
`orion.backtest.engine`, which replays history. This phase gives the forward
path an account. It adds no execution, no journal write and no reflection.

**Persistence is CLI-only, which is what makes it possible at all.** Mission 10
Phase 5.5 refused to wire a journal into the turn path and its decisive argument
was not cost — it was that `OrionInterface.tsx` renders "Nothing is recorded,
stored or logged" to the user, and persistence would have made that false while
leaving it on screen. That sentence is still on screen and is still true: the
HTTP path constructs no store, and
`tests/interface/test_security.py::test_a_completed_turn_writes_nothing_to_disk`
still runs a real text turn and a real audio turn against an isolated working
directory and asserts `list(rglob("*")) == []`. Nothing in `orion.interface`
changed. Neither did anything in `orion.orchestrator`.

**No import wall was moved, because none needed to be.**
`tests/orchestrator/test_import_hygiene.py` bans `orion.memory`, `sqlalchemy`
and `sqlite3` from the orchestrator and allows `wiring.py` exactly three extra
modules, none of them a store. It did not have to be edited: `wiring.py` already
imports the `PortfolioStore` *ABC* from `orion.paper.store` and already takes one
as a parameter, so the *concrete* `SqlitePortfolioStore` is constructed in
`orion/cli.py` — a composition root, like `app.py` — and handed through the
existing seam. A committed test now pins that: `SqlitePortfolioStore(` is
constructed in exactly one file in `src/`, and it is the CLI.

**The seam is binary, so the opt-in is explicit.**
`orion.memory.engine.create_memory_engine` runs `metadata.create_all`, so a
database file and its tables appear merely by constructing a store. There is no
"wire it but write nothing" middle position — the same fact Phase 5.5 recorded.
So `analyze` is unchanged unless `--account` is given: without the flag no store
is constructed, `create_memory_engine` is never reached, and the risk panel
still reports `no_portfolio_store` honestly. `orion account show` and
`analyze --account` check for the file *before* opening an engine, so neither can
bring a database into existence as a side effect of being asked to read one. A
command that refuses by first creating the thing it is refusing to read has not
refused, and every refusal path in the suite asserts zero files.

**Retention: indefinite, and deletion is the user's own act.** `orion.db` is
user-owned local paper-trading state. There is no cleanup, expiry, rotation,
compaction or background job anywhere in this phase, and no verb deletes or
resets an account. The store is append-only — one permanent row per revision, by
Mission 2's design — so the file grows without bound as the account accumulates
revisions. That is the correct trade for an audit trail and it is said out loud
in the CLI's own docstring rather than silently managed. This closes the second
of Phase 5.5's three unblock conditions ("a decision about retention"); the
third, the crash-consistency boundary Mission 5 recorded as Finding A, is not
reached by this phase because nothing here writes a journal.

**Deleting an account means deleting the whole SQLite set, and the count is not
fixed.** WAL mode means an open account is `orion.db` plus `orion.db-wal` and
`orion.db-shm`. A clean shutdown checkpoints the log and removes both sidecars,
so a properly-closed account is a single file — but a crash, or a process still
holding it, leaves them behind, and removing only the database would leave a
write-ahead log a later open could try to recover from. Both the refusal message
and the docstring therefore say "any -wal and -shm files beside it" rather than
promising three. A test performs the documented procedure and proves the next
`open` starts genuinely fresh at revision 0 rather than recovering the old
account.

**No argument can become a filesystem path.** The database location is
`ORION_DB_PATH` and nothing else; no value from `argv` reaches a path.
`--symbol` goes through `orion.orchestrator.intent.parse_intent`, whose
`Intent.symbol` is a value looked up in `_SYMBOL_ALIASES` rather than a
substring of the input — so `../../etc/passwd`, `/etc/passwd`, `:memory:` and a
UNC path are all refused as unknown symbols, and none of them creates a file
anywhere. `"gold; rm -rf /"` is the interesting case and is *accepted*: it
legitimately contains a supported alias, so the closed table resolves it — to
`XAUUSD`, never to any part of what was typed. Nothing is executed and the
account still lands at `ORION_DB_PATH`. That is the table working as designed,
and it has its own test saying so.

**`--balance` is validated before anything is created.** `nan` and `inf` both
parse as floats and `inf` would satisfy `PortfolioState`'s `gt=0` outright, so
both are refused by an `argparse` type callable along with zero, negatives and
non-numbers. Option-shaped tokens (`-inf`, `--symbol`) are refused by argparse
one step earlier. Every path exits 2 with no traceback, creates no file, and
does not echo the rejected value back.

**Failures name nothing.** `PortfolioStoreError`'s own messages embed SQLAlchemy
text that can name the database and its path, and an unreadable directory raises
an `OSError` that names it outright. As on the analysis path, the exception is
neither bound, formatted nor re-raised: four fixed sentences cover "no account",
"already exists", "could not read" and "could not open". Tests assert that a
store failure carrying a `sk-live-…` credential in its message prints none of
it, that no account output contains a file path, and that no credential reaches
either stream on any account path.

**Three existing tests in `tests/test_cli.py` were narrowed, not deleted, and
each gained a stricter companion.** They asserted the absence of exactly what
this phase adds, so they could not both survive unchanged and let it exist.
`test_the_only_verb_is_analyze` became
`test_the_verb_surface_is_exactly_analyze_and_account`, plus a new
`test_no_verb_or_flag_offers_live_execution` proving `trade`, `execute`, `order`
and `--live` are all still rejected — the half of Mission 12.4's rule that must
never be narrowed. The import allowlist gained four *exact modules*
(`orion.memory.engine`, `orion.memory.portfolio_store`, `orion.paper.store`,
`orion.risk.portfolio`), mirroring the orchestrator hygiene test's own
module-by-module style, plus a new test banning any wholesale
`orion.memory`/`orion.paper`/`orion.risk`/`orion.interface` import. And
`test_the_cli_touches_no_filesystem`'s `open`/`Path`/`pathlib` parameters — a
substring scan that also flagged the identifier `account_open` — were replaced
by two AST tests: no `open`/`read_*`/`write_*`/`unlink` call anywhere in the
file, and `Path(...)` may only ever call `.is_file()`. `shutil` and `tempfile`
remain banned outright.

**Two factory seams rather than one widened one.** `ServiceFactory` keeps its
zero-argument shape because that is what every existing caller and test passes;
the `--account` path takes a separate `AccountServiceFactory`. Widening the
first would have edited tests that are not about accounts at all. Neither seam is
reachable from the command line, and `store_factory` is handed the path
`_account_db_path` resolved from `ORION_DB_PATH` — it cannot redirect the
database.

**Known limitation, recorded rather than fixed.** `main()` does not dispose the
SQLAlchemy engine it opens. As a console entrypoint the process exits and the
operating system releases the file, so this is invisible in real use; in-process
callers (the test suite) hold the database open until the engine is garbage
collected, which is why the deletion test owns and disposes its own engine
through the approved seam. If a later phase ever calls `main()` in a loop, or
4.2b holds an account open across an execution cycle, engine lifetime becomes a
real concern and belongs there.

**Deliberately not in this phase:** paper execution, any journal write,
reflection, closed-position production, an account `delete`/`reset` verb,
telemetry, logging, and anything resembling live or broker execution. `README.md`
is unchanged.

**Files added:** `tests/test_cli_account.py` (46 tests). **Files modified:**
`src/orion/cli.py`, `tests/test_cli.py`, and
`tests/orchestrator/test_local_response.py` — the last a one-line
formatting-only correction of a `ruff format` violation introduced in `6aa6bc3`,
which `ruff check` does not catch but the CI gate does. No frontend file, no
`orion.interface` file, no `orion.orchestrator` file and no dependency manifest
was touched, and no dependency was added.

**Verification:** `pytest` 1700 passed (1653 before), `ruff check .`,
`ruff format --check .` (181 files) and `mypy src --strict` (81 files) all clean.
The interface security and import-hygiene suites and the orchestrator
import-hygiene suite pass unchanged. Proven against an isolated working
directory rather than claimed: an HTTP text turn creates no file, `orion analyze
XAUUSD` without the flag creates no file, and `orion account open` creates the
database — and a real `SqlitePortfolioStore` handed to the real `run_analysis`
produces `RiskAvailability.AVAILABLE` with a verdict on it, which is the first
time that has been true outside a backtest.

## 2026-08-29 — Mission 4 Phase 2b: the forward paper loop, and the journal's first production writer

**Mission 2 built an append-only journal that nothing had ever written to.**
Every `record_*` call in the repository lived in `tests/memory/` and
`tests/reflection/`; `orion.paper` had exactly one consumer anywhere in `src/` —
`orion.backtest.engine`, which replays history — so no `Fill` and no
`ClosedPosition` had ever been produced by a system running forward in time.
Phase 5.5 named the unblock condition precisely: "an execution loop that
produces real `Fill`s and `ClosedPosition`s, so the journal's write surface has a
producer". `src/orion/paper/session.py` is that loop, `orion paper XAUUSD` is its
only caller, and it is deliberately the only production writer that exists.

**Two enforced contracts shaped where it could live, and both were reported
before any code was written.** The specification asked for the loop in
`orion/paper/session.py`, which turned out to be blocked twice over.

First, `tests/orchestrator/test_import_hygiene.py::test_no_engine_package_imports
_the_orchestrator` lists `paper` among the engine packages forbidden from
importing `orion.orchestrator`, on the stated grounds that an import in that
direction "would invert the layering every other hygiene test in this repo
protects". `AnalysisReport` is an orchestrator type. Resolved without touching
the wall: `run_cycle` takes the three domain objects an analysis produced —
`ProposedDecision`, `Evidence`, `RiskVerdict` — and `orion.cli`, which may see
both sides, unpacks the report and hands them over. The session never learns that
an `AnalysisReport` exists. This is better layering than the shape originally
specified, not a workaround for it.

Second, `orion.memory` already depends on `orion.paper` in three files
(`journal.py` and `sqlite_journal.py` import `Fill`/`ClosedPosition`,
`portfolio_store.py` imports `PortfolioStore`), so importing `DecisionJournal`
back would have turned a clean one-way dependency into a cycle. No test forbade
it, which is exactly why it was raised rather than done. Resolved by dependency
inversion: `JournalWriter` is a two-method `Protocol` declared in
`session.py` — `record_fill` and `record_closed_position`, nothing else — which
`DecisionJournal` and `SqliteDecisionJournal` satisfy structurally with no change
to either. It restates an interface, never an implementation. Three tests keep
the restatement honest: the ABC satisfies it, the concrete class satisfies it,
and it declares exactly those two names, so a widened `Protocol` or a drifted
signature fails rather than rots.

**The Risk Guardian's authority is enforced twice, deliberately.** `execute()`
already raises `PaperExecutionError` unless `verdict.outcome` is APPROVED, so a
veto cannot produce a `Fill` even if this module were wrong; `run_cycle` also
refuses before building anything, so the common case reports a verdict rather
than a fault. A `None` verdict — the Guardian produced no answer at all — is
`NO_VERDICT`, which is not a veto and is not treated as approval either. Tests
cover VETOED, NO_TRADE_PROPOSED and absent, and each asserts zero saves and zero
journal calls, not merely a zero fill count.

**THE ACCOUNT EXECUTED AGAINST IS PROVEN TO BE THE ONE THAT WAS REVIEWED.** This
is the subtlest thing in the phase and was found by reading
`orion.orchestrator.analysis._risk_panel` rather than by testing. The Guardian
does not review the stored account: the analysis layer marks it to market first —
a pure, unsaved projection — and reviews *that*, so `verdict.portfolio_ref`
fingerprints the projection and not the record. `execute()` verifies exactly that
fingerprint. Passing the raw stored state would therefore have failed on every
approved trade. `run_cycle` reproduces the projection with the same pure
function and the same `now` and `last_price` the analysis used, and then
re-verifies the fingerprint itself before executing, failing closed as
`PORTFOLIO_MISMATCH` if it differs. That guard is not redundant with
`execute()`'s: it makes the failure legible instead of an exception, and it must
never be relaxed into executing anyway — the Guardian approved a specific account
state, and a mismatch means this is not it. It fired for real during
verification, when a harness passed a different `now` than the CLI had, which is
precisely the class of drift it exists to catch.

**Persistence order: the account first, the journal second, always.** Mission 5's
Finding A instructed a future orchestrator to "treat `PortfolioStore` as
authoritative and persist it first; call `journal.record_fill()` second", because
the two stores commit independently and only that ordering is recoverable. If
`save()` fails, nothing is journaled and no trade happened. If the journal write
fails after a successful `save()`, the trade *did* happen: it is reported as
`JOURNAL_UNAVAILABLE` rather than rolled back, because there is no transaction
spanning both stores and inventing one by re-saving the previous state would be a
second write that could itself fail. The ordering is asserted as a recorded call
sequence — `["load", "load", "save", "record_fill"]` — not inferred from reading
the code.

**Replay after a crash is safe, and that was verified rather than assumed.**
`sqlite_journal._write` uses `on_conflict_do_nothing` keyed on the *content*
fingerprint, so re-recording an identical `Fill` is a no-op returning the same
fingerprint: one row, whatever the recorded_at. The existing integrity guarantee
is untouched — the same fingerprint with different stored bytes still raises
`DecisionJournalIntegrityError` — and there is a test for each half, so
idempotency cannot later be mistaken for "the journal accepts anything".

**Engine lifetime is now explicit, which 4.2a recorded as an open limitation.**
A cycle holds an account open across a read, an execution and two writes, and a
leaked pooled connection keeps SQLite's `-wal`/`-shm` sidecars locked so a user
cannot delete them. `_default_paper_resources` is a context manager that builds
one engine, yields the store and the journal on it, and disposes in a `finally` —
on the exception path too. One engine on purpose: two would mean two pools
competing for the same single SQLite writer lock the store's compare-and-swap
safety already rests on. Sharing it does not make the two writes one transaction;
Finding A is explicit that they commit independently, which is why the ordering
above matters. The two *account* verbs still leave their engine to process exit,
unchanged from 4.2a and harmless for a command that immediately exits — recorded
here rather than quietly assumed.

**Nothing is fabricated.** The fill price and fee come from `execute()` under the
configured `PaperExecutionPolicy`; the quantity from `compute_position_size`, the
same shared function the Guardian's own `position_size_within_limits` control
used to approve the proposal; the stop and target from the approved proposal; the
balances from `execute()`'s own cash-conservation arithmetic. A `ClosedPosition`
is written only when `resolve_stop_take_profit` actually produced one, and a
cycle that closes a position does not also open one — the same sequencing
`orion.backtest.engine` uses, because the evidence that closed it is the evidence
the proposal was reasoned from and acting twice on it would double-count. A test
asserts the journalled `Fill` is the very object `execute()` returned, by
identity rather than equality, which is Finding B's stated long-term mitigation
for `Fill` provenance.

**One cycle per invocation, and no venue.** `orion paper XAUUSD` runs once and
exits: no scheduler, no daemon, no retry, no thread, no `while True`, asserted by
a test that greps the module for all of them. There is no `--live`, no broker
adapter and no code path toward one; anything recurring is a cron entry the user
wrote. An account is required and never created here — opening one is a
deliberate act with a balance the user chooses.

**Existing tests changed, and why each had to.** Four assertions in
`tests/test_cli.py` and `tests/test_cli_account.py` asserted the *absence* of
what this phase adds. The verb-surface assertions gained `paper`; the CLI's own
import allowlist gained five entries, named module by module in the same style
the orchestrator hygiene test uses; and `test_no_execution_verb_exists_yet` —
written in 4.2a with the comment "4.2b adds `orion paper`. Until it does, it must
not be reachable" — was replaced by one asserting the verb exists, takes a symbol
from the closed table, and still rejects `--live`. **No import-hygiene boundary
was weakened**: the orchestrator, paper and interface hygiene suites are
byte-identical and pass unchanged.

**Deliberately not in this phase:** reflection, `record_evidence`/`record_
reasoning`/`record_decision`/`record_verdict` (the journal's contract makes none
of them a prerequisite for `record_fill`), an account `delete`/`reset` verb,
telemetry, logging, scheduling, and anything resembling live or broker execution.
`README.md` is unchanged.

**Files added:** `src/orion/paper/session.py`, `tests/paper/test_session.py`
(29 tests). **Files modified:** `src/orion/cli.py`, `tests/test_cli.py`,
`tests/test_cli_account.py`. No frontend file, no `orion.interface` file, no
`orion.orchestrator` file and no dependency manifest was touched, and no
dependency was added.

**Verification:** `pytest` 1740 passed (1701 before), `ruff check .`,
`ruff format --check .` (183 files) and `mypy src --strict` (82 files) all clean.
The interface security, interface/orchestrator/paper import-hygiene, memory and
backtest suites all pass unchanged. Proven on disk rather than claimed: a real
cycle against a real `SqlitePortfolioStore` and a real `SqliteDecisionJournal`
produced `executed`, `long 31.645570 @ 2521.3562 fee 7.9790`, one row in
`fill_table`, portfolio revisions `[0, 1]` — and replaying the same fill left one
row with an identical fingerprint. After `dispose()` only `acct.db` remained, the
WAL sidecars checkpointed away, so no handle was left holding the file. From an
isolated working directory: `orion paper XAUUSD` without an account exits 5 and
creates nothing, an HTTP turn still writes zero bytes, a hostile symbol is
refused by the closed table, and `--live` is rejected by the parser.

## 2026-08-30 — Mission 4 Phase 2c: the chain that authorised the trade, and Reflection's first real trade

**Mission 5 had never run on a trade this system made.** `reflect_on_closed_
position` walks `ClosedPosition -> RiskVerdict -> ProposedDecision` and fails
closed on a missing link. Phase 2b's `run_cycle` journaled a `Fill` and a
`ClosedPosition` and nothing else — so every `ClosedPosition` ORION had ever
produced referenced a verdict that was in no journal, and Reflection would have
raised `ReflectionError` on all of them. It had only ever been exercised against
hand-built chains in `tests/reflection/`, and it had no caller anywhere in
`src/`. Both halves of that are what this phase fixes: the forward cycle writes
the four records the chain needs, and `orion reflect` reads them back.

**`JournalWriter` is no longer two methods, and the narrowness it lost was never
a safety property.** 2b's Protocol declared `record_fill` and
`record_closed_position` on the stated grounds that "this module produces
`Fill`s and `ClosedPosition`s and nothing else". That was a true statement about
what that phase produced, not a boundary protecting anything, and it stopped
being true the moment a fill needed its authorisation on record. It is now the
full *write* surface of `orion.memory.journal.DecisionJournal` — six methods,
still restated rather than imported, because `orion.memory` already depends on
`orion.paper` and importing back would be a cycle. The property that does
matter is kept and is now asserted directly: not one read, update or delete. A
forward cycle appends what it did; reading history back is `orion.reflection`'s
job and there is no method here through which the session could do it.

**Leaf-first, and the fill last — the order is the contract.** Evidence,
reasoning, decision, verdict, then fill. Each record is written only after
everything it references is already stored, so an interrupted write leaves a
*shorter* chain rather than a broken one: there is no point at which a journaled
record points at a fingerprint the journal does not hold. Asserted as a recorded
call sequence — `["load", "load", "save", "record_evidence", "record_reasoning",
"record_decision", "record_verdict", "record_fill"]` — not inferred from reading
the code. Finding A's ordering is untouched and still outermost: the account is
saved before any of it.

**A partially-written chain is never reported as written.** The chain goes in
inside the same `try` as the fill, so a journal that fails anywhere reports
`JOURNAL_UNAVAILABLE` once for the whole write. `CycleResult.audit_chain_
fingerprints` stays empty in that case even when two of the four records
landed — those are durable and cannot be undone, and re-writing them would be a
second write that could itself fail, but claiming the audit record exists when
it does not is the one thing that must not happen. Replay is safe for exactly
the reason 2b established: every record is content-addressed, so re-running the
cycle re-writes all six and the ones already stored are no-ops returning the
same fingerprints. A test proves that by pre-writing the chain and then running
the cycle.

**`ReasoningOutput` is journaled but never read.** `run_cycle` takes it as a
parameter and hands it to the journal; nothing branches on it, so a caller
cannot change what a cycle *does* by passing a different one. Reflection does
not need it — `ProposedDecision.reasoning_ref` already carries the action,
direction, confidence, risk level and levels — but `ReasoningRef` deliberately
excludes `rationale`, so without recording the `ReasoningOutput` itself the only
durable trace of what ORION actually said would be a hash of prose nobody kept.
Recording a reference without ever recording its referent leaves a chain whose
links point at nothing.

**A veto still journals absolutely nothing, and that is a decision rather than
an omission.** The chain is written on the executed path only. Journaling every
refused proposal is defensible and may well be a later phase, but a cron entry
running `orion paper` hourly would then write a chain per invocation, almost all
of them identical WAITs, with no consumer — and Mission 4 Phase 2a already
recorded that this database grows without bound and is never pruned. 2b's tests
asserting that a veto performs zero saves and zero journal calls are left
standing rather than narrowed, and a new test asserts the four chain writes are
absent too, so the decision cannot drift silently.

**A closing cycle writes no second chain.** The records that authorised a
position were journaled when it was opened. The closing cycle runs its own
analysis and produces its own decision and verdict, and neither of them closed
anything — `resolve_stop_take_profit` did, from the price — so journaling them
against the close would attribute it to a proposal that had no part in it.

**`orion reflect SYMBOL [--limit N]` is the read side, and it is read-only in the
precise sense.** It runs no analysis, contacts no vendor and needs no credential
(proven by a test that deletes both API keys from the environment and still
gets an answer). It records nothing and changes no account: a test runs `account
show` before and after and compares the output byte for byte, and another counts
journal rows before and after. It cannot bring a database into existence — like
`account show` it checks the file exists first, and a missing one exits 5 with
zero files created. The one honest caveat, stated in the module docstring rather
than glossed: `create_memory_engine` runs `metadata.create_all`, so opening an
*existing* database adds any table its schema is missing. That is a schema
repair on a file the user already has, it is the same seam every other verb here
opens through, and it is not a record.

**`ReflectionBatch` reports what it could not explain, by fingerprint.** A
`ClosedPosition` whose verdict or decision is missing cannot be reflected on,
and every position opened before this phase is permanently in that state.
Raising on the first one would let a single legacy trade hide every later
reflection; dropping them silently would make a journal with ten closed trades
and two reflectable ones look like a journal with two trades. So they are listed
by fingerprint — enough for a reader to fetch the record and see for themselves —
alongside `total_closed_positions`, so a short answer is distinguishable from an
exhausted one. A model validator asserts every examined trade lands in exactly
one of the two tuples.

**Corruption is not incompleteness.** `DecisionJournalIntegrityError` is never
collected into `unreflectable_fingerprints`; it propagates, and the CLI reports
`ORION could not read the trading journal` and exits 8. A journal whose contents
do not verify must never be presented as a journal with a couple of unexplained
trades in it. Tested by corrupting a stored payload directly through SQLAlchemy
and asserting the exit code and the empty stdout.

**The tail slice, and why the journal's `limit` was not used.**
`list_closed_positions_by_symbol` applies `limit` to a `closed_at` *ascending*
order, so passing it through would return the *oldest* trades under the name
"most recent". Rather than change a contract other callers depend on,
`reflect_on_recent_closed_positions` reads the symbol's closed positions and
takes the last `limit` — which is also where the honest total comes from. A test
seeds five trades, asks for two and asserts it got the last two. For a local
paper journal this is a bounded read; if it ever stops being one the fix belongs
in the journal's query surface, not in a narrower answer here.

**Nothing scores anything, at either layer.** Mission 5's load-bearing
constraint was that `TradeReflection` exposes facts and no quality verdict, so
nothing downstream can turn one closed trade into a strategy change. A batch is
where an aggregate would be most tempting and most damaging — a win rate or a
total P&L over several trades reads as evidence for changing strategy — so
`ReflectionBatch` carries counts and facts only, and `render_reflections` prints
no score, grade, win rate, average or recommendation. Both are asserted against
a blocklist rather than left to review. What is printed is the approval margin
beside the matched exit level, which is exactly what distinguishes a bad outcome
from a bad decision, with the reading left to the user.

**`orion.reflection` gained the import-hygiene test it never had.** Its module
docstring has claimed since Mission 5 that it is pure, takes a `DecisionJournal`
and "never a `PortfolioStore` in any form". Every comparable package —
`risk`, `paper`, `memory`, `research`, `backtest`, `analysts`, `orchestrator` —
already had an AST test proving its equivalent claim; this one did not, which
mattered little while nothing called it and matters now that something does. It
bans network, database, filesystem, clock and randomness imports, bans reaching
`orion.paper.store`/`session`/`execution`, `orion.memory.portfolio_store`/
`sqlite_journal`/`engine`, `orion.risk.guardian`, `orion.decision.engine`, the
AI providers, the orchestrator and the interface; asserts no `record_*` call
exists anywhere in the package; and asserts the dependency stays one-way, with
`orion.cli` as the single permitted consumer. The clock check walks the AST
rather than scanning substrings, because these modules' own docstrings say
"datetime.now()" while explaining why they never call it — the same correction
Phase 2a applied to the CLI's `open`/`Path` scans.

**No import-hygiene boundary was weakened.** The orchestrator, paper, interface,
memory and backtest hygiene suites are byte-identical and pass unchanged.
`orion.paper.session` still imports neither `orion.orchestrator` nor
`orion.memory`. The CLI's own allowlist gained three exact modules —
`orion.memory.journal` (the ABC), `orion.reflection.analysis`,
`orion.reflection.schemas` — plus `orion.paper.schemas` for the `CloseReason`
the renderer maps, named module by module in the established style, and
`orion.reflection` was added to the list of packages the CLI may not import
wholesale.

**Two pre-existing test defects were found and fixed; neither was caused by this
phase, and both were verified against `HEAD` before being touched.**
`tests/interface/test_analysis_turn.py::test_a_macro_read_crosses_the_wire_
without_its_provenance` was a time bomb: it builds a calendar snapshot with the
orchestrator suite's fixed `NOW` (2026-08-28) but the HTTP path runs
`run_analysis` with the real `datetime.now(UTC)`, and Mission 7's quality gate
blocks a snapshot retrieved more than 24 hours before `now` — so the macro panel
silently became `macro_unavailable` once the wall clock moved on, and the test
failed for every run after 2026-08-29. Fixed by retrieving the fixture snapshot
at the real `now`, which is what that path actually requires; the orchestrator
suite, which supplies its own matching `now`, is untouched.
`tests/test_cli_account.py::test_a_hostile_symbol_cannot_escape_the_closed_alias
_table` compared raw file listings across a command, but `-wal`/`-shm` are
created when a connection opens and removed when the last one closes, and the
account verbs leave their engine to process exit (the limitation 2a recorded) —
so the test was asserting garbage-collection timing and flaked on a different
parameter each run. Fixed with a `databases_in` helper that folds the sidecars
into their database, which asserts the property that actually matters: no new
database appeared.

**Deliberately not in this phase:** journaling refused proposals, persisting a
`TradeReflection` (it remains a pure function of durable journal data, so
storing it would be an optimisation and never a necessity — the Mission 5
position, unchanged), any aggregate or quality score, an HTTP or frontend
surface for reflection, an account `delete`/`reset` verb, telemetry, logging,
scheduling, and anything resembling live or broker execution. `README.md` is
unchanged and no frontend file was touched.

**Files added:** `tests/test_cli_reflect.py` (32 tests),
`tests/reflection/test_batch.py` (24 tests),
`tests/reflection/test_import_hygiene.py` (6 tests),
`tests/reflection/__init__.py` (a package, so its `test_import_hygiene`
basename no longer collides with `tests/memory`'s under pytest's rootdir
import mode). **Files modified:** `src/orion/paper/session.py`,
`src/orion/reflection/{analysis,schemas}.py`, `src/orion/cli.py`,
`tests/paper/test_session.py`, `tests/test_cli.py`,
`tests/test_cli_account.py`, `tests/interface/test_analysis_turn.py`. No
frontend file, no `orion.interface` file, no `orion.orchestrator` file and no
dependency manifest was touched, and no dependency was added.

**Verification:** `pytest` 1804 passed (1740 before), `ruff check .`, `ruff
format --check .` (187 files) and `mypy src --strict` (82 files) all clean.
Proven on disk rather than claimed: a real `run_cycle` against a real
`SqlitePortfolioStore` and a real `SqliteDecisionJournal` wrote the four chain
records and the fill, a later cycle closed the position at its take-profit, and
the real `reflect_on_closed_position` walked the result back to a
`TradeReflection` carrying `take_profit_hit`, `rr=2.51` against a `1.00`
minimum, a `+1.51` approval margin, `2613.98` realised and the opening fill's
own `7.98` fee. That is the first time Reflection has run on a trade ORION
actually made.

## 2026-08-30 — Mission 8 v2: the Technical Analyst, a knowledge registry, and the synthesis seam

**The Technical Analyst is deterministic, and that is the central decision.**
The obvious symmetry with Mission 8 v1 would have been a second Claude provider.
It was rejected: the Macro Analyst calls a model because economic releases are
prose someone has to read, whereas technical analysis over OHLCV is arithmetic —
an EMA crossing, a close beyond a swing, a three-bar imbalance. Asking a model to
*report* those replaces a reproducible measurement with a plausible one, and buys
a token cost, a latency, a failure mode and a second prompt-injection surface on
the analysis path in exchange for nothing. `analyze_technical` is a pure function:
same bars in, byte-identical findings out, asserted by a test. It needs no
credential, contacts nobody, and a test proves it by running with every API key
deleted from the environment.

**Knowledge is a registry, not a prompt.** `orion.analysts.knowledge` maps a
closed `TechnicalConcept` enum to a `ConceptDefinition` carrying the concept's
meaning, the exact deterministic rule ORION applies, the inputs that rule needs,
what the concept explicitly does *not* claim, and one canonical sentence per
`StructureBias`. A finding's `interpretation` is looked up from that table; its
`observation` is a fixed template filled with real numbers. The split is
enforced from both ends: a model validator on `TechnicalFinding` rejects any
interpretation that is not the registry's own text for that concept and bias — so
even a hand-assembled finding cannot carry invented commentary — and a test
asserts no interpretation contains a digit while every observation does. Every
`rule` string names the `orion.analysis` function that implements it, checked by
a test, so a definition cannot drift into describing an intention.

**Nothing was scraped and nothing is claimed that cannot be computed.** Every
definition is written from scratch and covers concepts that are public, decades
old and in universal use in charting software. `UNSUPPORTED_CONCEPTS` is the
other half of that honesty and is tested as carefully as the supported list:
VWAP, volume profile/OBV, MACD, order blocks, chart patterns, Elliott wave,
intermarket correlation and order flow, each with a concrete reason rooted in
this build rather than "not implemented yet". **VWAP and every volume-derived
construction are refused outright**: `OHLCVBar.volume` is optional and routinely
absent for spot metals, and `bars_to_dataframe` substitutes `0.0` when it is, so
a VWAP would be arithmetic on a placeholder presented as a measurement. No
function in `orion.analysis.price_action` reads `volume` at all, proven by a test
that runs every detector with volume absent, zero and large and asserts identical
results. **MACD was also refused, for a different reason**: adding it would change
`IndicatorSnapshot`, which sits inside the content-fingerprinted `Evidence` that
every journaled `ProposedDecision` and `RiskVerdict` references, so it belongs to
a deliberate change of that record and not to this layer.

**The new primitives live in `orion.analysis`, not in the analyst, and not on
`Evidence`.** `price_action.py` adds indexed swings, fair value gaps, breaks of
structure, changes of character, liquidity sweeps and Fibonacci anchors as pure
functions over bars — beside `indicators.py` and `structure.py`, where every
other deterministic primitive already lives. Deliberately *not* fields on
`Evidence`: that object is fingerprinted and journaled, so adding to it would move
the hash of every future decision and verdict for reasons unrelated to any of
them. **No calculation was duplicated.** `structure.find_swing_points` now
delegates to `price_action.find_swings` and keeps its exact "two lists of prices"
contract; a test asserts the two views agree, and the existing structure suite
passes unchanged.

**Look-ahead bias is prevented by construction.** A fractal swing at bar `i` is
not knowable until bar `i + lookback`, so `SwingPoint` carries
`confirmed_at_index` and the break/sweep detectors compare a bar only against
swings confirmed strictly before it. A dedicated test builds a series where a bar
both confirms a swing and closes beyond it, and asserts no break is reported —
Mission 6 lists look-ahead checks as required validation, and this is that check
for this module.

**Absence is reported, never filled in.** Every concept ends up in exactly one of
two lists per timeframe: a `TechnicalFinding`, or an `UnevaluatedConcept` with a
closed reason (`INSUFFICIENT_BARS`, `INDICATOR_UNAVAILABLE`, `NO_CONFIRMED_SWINGS`,
`NO_VALID_ANCHOR`). `TechnicalTimeframeReport`'s own validator enforces that, so a
concept cannot silently vanish. The distinction that matters is preserved:
"ORION looked and found no gaps" is a NEUTRAL finding with a count of zero;
"this window is too short to hold three bars" is unevaluated. Fibonacci reports
nothing without two distinct, ordered, opposing confirmed swings, and reports no
retracement *depth* when price has left the anchor range — a move already exceeded
is not being retraced.

**MULTI-TIMEFRAME IS A LIST, NOT A HIERARCHY.** `analyze_technical` takes one
observation per timeframe, reports each separately and states whether the
directional ones agree. It deliberately does *not* weight higher timeframes more
heavily: that convention is real and widely held, but this build has run no study
that would justify a specific weighting, and inventing one would repeat exactly
the uncalibrated-threshold problem `classify_volatility`'s own docstring already
warns about. A one-all disagreement is reported as `timeframe_agreement=False`
with a NEUTRAL aggregate, never averaged into a direction.

**THE SYNTHESIS SEAM COMPARES SIGNIFICANCE, NOT DIRECTION — and this is the
subtlest decision in the milestone.** The tempting move is to fuse the technical
bias with `MacroPolicyPressure` into one directional call. ORION does not, because
`MacroPolicyPressure` is deliberately *not* asset-direction vocabulary (Mission 8
v1 recorded that choice explicitly): turning "tightening pressure" into "bearish
for gold" requires an asset-specific economic assumption this build has never
established, tested or calibrated. So the axis is one both specialists genuinely
measure — how *eventful* their own domain is. Technical is ACTIVE on a HIGH
volatility regime or any detected break/sweep, QUIET on LOW volatility with no
events, UNDETERMINED otherwise; macro reads ACTIVE/QUIET/UNDETERMINED off
`MarketRelevance`. Those corroborate ("the calendar is heavy and price is already
moving") or conflict ("a major release is landing and price has done nothing
yet"), which is a real, checkable comparison and the only one ORION can make
honestly today. A test asserts the technical bias is identical across all five
`MacroPolicyPressure` values.

**Neither side is discarded, and provenance survives.** A conflict carries both
specialists' own readings, and `MarketThesis` keeps the technical bias, every
timeframe's bias/volatility/finding counts/evidence fingerprint/provider/retrieval
time/bar count, plus the macro pressure, relevance, confidence, model, prompt
version, input fingerprint, source provider, window and the Macro Analyst's
`rationale` verbatim. The macro half is all-or-nothing under a validator, so a
partial read cannot be presented as a complete one, and `summary` must equal the
canonical sentence for its alignment — invented prose is rejected by the model.

**THE RISK GUARDIAN IS UNTOUCHED, AND IT IS STRUCTURAL RATHER THAN ASSERTED.**
Neither `TechnicalAnalystOutput` nor `MarketThesis` has a field capable of
expressing an action, direction-to-trade, quantity, entry, stop, target, risk
parameter or approval — checked against a blocklist across every model in both
modules, the same technique Mission 8 v1 applied to the Macro Analyst's schema.
`orion.decision`, `orion.risk`, `orion.paper`, `orion.memory`, `orion.reflection`
and `orion.backtest` still cannot import `orion.analysts` at all. Both panels are
computed *after* the decision and the verdict already exist, and a test serialises
the whole evidence/reasoning/decision/risk chain and compares it byte for byte
across every technical and macro state. Paper execution is unchanged and still
runs only behind an APPROVED verdict.

**ONE IMPORT WALL MOVED, DELIBERATELY, AND IT GOT NARROWER FOR EVERY FILE THAT
DID NOT NEED IT.** `tests/analysts/test_import_hygiene.py` banned `orion.analysis`
from `orion.analysts` under the heading "downstream layers". That was a
misclassification: `orion.analysis` is *upstream* of everything — pure computation
over bars, importing only `orion.data` — and the Macro Analyst simply had no
reason to read it. A specialist over deterministic market computation must read
that computation; the alternative was reimplementing every indicator inside this
package. The blanket ban became a per-file allowlist naming three read-only
modules (`evidence`, `structure`, `price_action`) for `technical.py`,
`technical_schemas.py` and `synthesis.py` — the same idiom the orchestrator's own
hygiene test uses for `wiring.py`. The macro files still cannot reach
`orion.analysis` at all, no file may import it wholesale, and three new tests were
added: one asserting the decision/execution chain is banned from *every* file with
no carve-out, one checking the allowlist is used in both directions (an unused
permission must be deleted), and one banning a wholesale import. `orion.interface`
and `orion.orchestrator` were added to the ban list. `orion.config` was
deliberately *not* added — `claude_macro_provider.py` has read `Settings` since
Mission 8 v1 and banning it here would be an unrelated tightening. **Every ban
that protects the Risk Guardian's authority is untouched, and the direction stays
acyclic and one-way**: the inbound rule still forbids `orion.analysis` from
importing back.

**Wired into the orchestrator, and stopping there.** Mission 7/8 v1 shipped the
Macro Analyst with zero integration and the repository later recorded the cost:
"two missions had been built and left unconnected". So `run_analysis` produces
both panels on every analysis, and `AnalysisReport` gained `technical:
TechnicalPanel` and `thesis: ThesisPanel`. `TechnicalPanel` has no NOT_CONNECTED
state, because a deterministic specialist cannot be missing a credential. **No
second market-data fetch**: `TimeframeObservation` pairs the snapshot the analysis
already has with the `Evidence` already built from it, and its validator refuses a
mismatched pair — so every finding carries the same fingerprint
`decision.evidence_ref.content_hash` does, verified end to end by a test. The
orchestrator's per-file allowlist gained four exact modules, all of them pure and
none constructing a client. **`orion.interface` is byte-identical and no field
crosses the HTTP boundary**, so the wire contract and the frontend are unchanged;
surfacing any of this to a client is a separate decision.

**Two pre-existing quirks encountered, neither changed.** The shared test series
rises monotonically and therefore contains no fractal swing at all, which is why
the analyst honestly reports five concepts as `NO_CONFIRMED_SWINGS` on it — the
"do not pretend" behaviour working, not a defect. And `classify_volatility`'s
thresholds remain the uncalibrated placeholders its own docstring describes; the
knowledge registry's volatility entry inherits that caveat in writing rather than
hiding it.

**Deliberately not in this milestone:** any LLM call in the technical path, MACD
or any new indicator on `Evidence`, VWAP or any volume-derived concept, News
Analyst (still blocked on a missing data source, unchanged from v1), the six other
Mission 8 roles, persistence of findings or theses, an HTTP/frontend surface for
either panel, multi-timeframe fetching in the orchestrator, telemetry, logging,
and anything resembling live or broker execution. `README.md` is unchanged.

**Files added:** `src/orion/analysis/price_action.py`,
`src/orion/analysts/{knowledge,technical,technical_schemas,synthesis}.py`,
`tests/analysis/test_price_action.py` (37 tests),
`tests/analysts/{test_knowledge,test_technical,test_technical_schemas,
test_synthesis}.py` (136 tests), `tests/orchestrator/test_technical.py`
(22 tests). **Files modified:** `src/orion/analysis/structure.py` (delegates its
fractal detection), `src/orion/orchestrator/{analysis,schemas}.py`,
`tests/analysts/test_import_hygiene.py`,
`tests/orchestrator/test_import_hygiene.py`. No frontend file, no
`orion.interface` file, no `orion.decision`/`orion.risk`/`orion.paper`/
`orion.memory` file and no dependency manifest was touched, and **no dependency
was added**.

**Verification:** `pytest` 2031 passed (1806 before), `ruff check .`, `ruff
format --check .` (198 files) and `mypy src --strict` (87 files) all clean. Every
import-hygiene suite in the repository passes (74 tests), as do the paper, memory,
backtest, risk, reflection and interface-security suites. Proven rather than
claimed: a real `run_analysis` over the shared series produced five technical
findings whose `evidence_fingerprint` equals both the report's evidence
fingerprint and `decision.evidence_ref.content_hash`, a thesis was formed, and the
serialised decision and risk objects were byte-identical with and without the
Macro Analyst connected.

## 2026-08-31 — Mission 8 v2.1: the specialists reach a reader, and thesis persistence is refused

**Phase A surfaced what v2 had computed and left unreachable.** The Technical
Analyst and the `MarketThesis` existed only inside `AnalysisReport`, which is
returned to a caller and dropped. `AnalysisSummary` gained exactly two fields —
`technical` and `thesis` — and `orion.cli.render_report` gained a specialist
section. Everything else on the wire is byte-identical, and the shape test that
pins the payload was *widened by two keys and then made stricter*: it now also
pins the nested shape of every new object, because a fingerprint or a vendor
name would most plausibly appear there without anyone noticing.

**THE WIRE CARRIES LESS THAN THE TERMINAL, DELIBERATELY.** Two values are
dropped at `orion.interface.service` and only there: the 64-character evidence
fingerprint and the market-data provider's name. Both were already governed —
`_to_analysis_summary` has always dropped the evidence fingerprint and
`_to_macro_summary` the model identifier, on the stated principle that a browser
learns what ORION concluded and never from exactly which bytes or which vendor.
Shipping `source_provider` would additionally have broken an existing security
assertion (`"twelvedata" not in blob`), which is the same rule arriving from a
second direction. A test asserts no 64-hex string and no vendor name appears
anywhere in an analysis payload. `orion.cli` prints both, because a local
operator reading their own machine is exactly the party who needs the
fingerprint — it is the only thing proving a finding describes the same bars the
proposal was reasoned from, and a CLI test asserts it equals
`decision.evidence_ref.content_hash`.

**Two version strings *do* cross, and that is a deliberate divergence from
`MacroSummary`.** `analyst_version` and `knowledge_version` reach a client while
macro's `prompt_version` does not. The distinction is real rather than an
oversight: a prompt version is only meaningful beside the model identifier this
boundary hides, whereas these two identify ORION's own committed detectors and
wording. They are what lets a reader confirm an interpretation was *looked up*
rather than generated, and they name nothing external.

**The observation/interpretation split survives serialisation, and is tested at
the boundary.** A test asserts every wire `observation` contains a digit, every
wire `interpretation` contains none, and every interpretation string is a member
of the knowledge registry's own committed set — so a client cannot receive
market commentary ORION did not author. The per-finding supporting `values` are
deliberately *not* shipped: every number is already inside the formatted
`observation`, and sending both would put one measurement on the wire twice in
two shapes that could drift.

**Absence still reads as absence at the boundary.** `unevaluated` crosses with
its reason per concept, and a test re-checks the engine's completeness invariant
*after* serialisation: every concept appears exactly once per timeframe as
either a finding or an unevaluated entry. A separate test asserts no unsupported
concept — VWAP, MACD, OBV, volume profile, order blocks, Elliott wave — appears
anywhere in a payload or in the terminal rendering, in any casing, as though it
had been detected.

**The Risk Guardian boundary is unchanged and re-asserted at the new surface.**
`TechnicalSummary`/`ThesisSummary` and their four nested shapes carry no action,
direction-to-trade, quantity, entry, stop, target or approval field, checked
against the same blocklist the analyst schemas already use. `RiskSummary`'s
shape is pinned unchanged, and a test asserts no verdict vocabulary
(`approved`/`vetoed`/`no_trade_proposed`) appears inside either specialist
panel. `orion.interface` still cannot import `orion.analysts` — every enum is
read through the report as `.value` — so no import wall moved in Phase A.

**The interface still writes nothing.** A dedicated test runs a full analysing
turn against an isolated working directory and asserts `rglob("*") == []`, and
the pre-existing structural test proving `orion.interface` opens no file and
imports no database library is unchanged.

**One substring ban became an AST check, for the reason Phase 2a already
recorded.** `test_the_cli_has_no_dangerous_call` matched the *substring* `eval`,
which the identifier `unevaluated` contains. That is the same false positive
Mission 4 Phase 2a fixed when `open` flagged `account_open`, and the same fix
applies: `subprocess`/`os.environ` stay substring bans, and `exec`/`eval`/
`compile` moved to an AST call scan that is strictly stricter — it also bans
`__import__`, `globals`, `locals`, `getattr`, `setattr` and the modules that
would provide an evaluator. A test that fails on a field name is not testing
what it claims to.

---

**PHASE B: `MarketThesis` IS NOT PERSISTED, AND THIS IS THE REFUSAL.** The
instruction was to persist only if it is safe, and to stop and document
otherwise. Three of the six stated criteria pass cleanly and were verified
rather than assumed: a new table is additive so no historical journal semantics
change; `orion.analysts` imports neither `orion.memory` nor `orion.paper`, so
there is **no import cycle**; and `MarketThesis` is frozen, so `canonical_hash`
would give it a content-addressed primary key exactly like the six existing
records, with the interface untouched and the existing retention policy
inherited. Persistence is refused for two other reasons.

**Blocker 1 — THE LINK DOES NOT EXIST, AND WAS DEMONSTRATED, NOT ARGUED.**
`MarketThesis` carries no `analysis_id`, no `decision_fingerprint` and no
`risk_verdict_fingerprint`; its only fingerprint is
`ThesisTimeframe.evidence_fingerprint`. The mission's own requirement — that
reflection be able to recover the thesis for a closed trade — therefore has only
one available key, and that key is not unique. Two analyses inside the same bar
(a cron at :05 and again at :35 is the ordinary case) produce two theses with
**identical `evidence_fingerprint` and different content hashes**: two
permanent, non-duplicate rows answering the same reverse-index lookup, with
nothing on either saying which trade it authorised. That was run and confirmed
before this entry was written. Reflection would have to guess, and this codebase
raises on that ambiguity rather than guessing (`_find_opening_fee` already
does). The obvious fix — putting a decision reference on the thesis — inverts
the ordering that keeps the thesis advisory: Mission 8 v2 computes it *after*
the decision and the verdict precisely so it cannot be an input to either, and a
thesis that referenced a decision would couple the advisory record to the
authority chain in the one direction that must stay closed.

**Blocker 2 — THE ONLY CORRECT WRITER IS THE ONE THAT MAY NOT SEE AN ANALYST.**
Preserving the mandated account-save-before-journal ordering means the thesis
write belongs inside `orion.paper.session._persist`, alongside the chain
Phase 2c already writes there — which requires `orion.paper` to import
`orion.analysts`. That is forbidden by the analysts inbound wall, and it is the
one wall that should not move: `orion.paper.execution` is the last step of the
decision chain, and an import in that direction puts specialist output one
attribute access away from the thing that acts on an APPROVED verdict. The
existing escape hatch does not transfer. `JournalWriter` inverts
`orion.paper` → `orion.memory` with a `Protocol`, but a `Protocol` restates
*behaviour*; a `record_thesis` method still has to name its payload type, and
`SqliteDecisionJournal` has to hash and serialise a real `MarketThesis`. The
alternative — writing the thesis from `orion.cli` after `run_cycle` returns —
was considered and rejected: it creates a second production journal writer,
breaking Mission 5's Finding B "one production writer" guarantee, and opens a
partial-write state `CycleResult` cannot express.

**A standalone analysis must not start filling the journal, and does not.**
Nothing in Phase A constructs a store, a journal or an engine. `orion analyze`
without `--account` still creates no file, the HTTP path still writes zero
bytes, and both are asserted against isolated working directories.

**What would unblock it, concretely.** (1) A stable identity for a thesis within
the decision chain that does not point *at* the decision — the natural candidate
is the `analysis_id` the orchestrator already mints and stamps on
`ProposedDecision` and `RiskVerdict`, which would let a thesis be found by the
same key without referencing the proposal. That is a change to `synthesize`'s
inputs and belongs to its own milestone. (2) A home for the `MarketThesis`
*schema* that both `orion.paper` and `orion.memory` may already see, so the
writer can stay where the ordering guarantee lives without moving the analysts
wall — a schema-relocation decision, not a side effect of a persistence one.
Until both exist, a thesis remains a pure function of two analyst outputs,
recomputable only while the bars are still in hand, and recorded here as
deliberately transient — the same position Mission 5 took on `TradeReflection`
and for the same reason: no consumer yet needs it durable, and building the
schema ahead of that need is what this project has declined at every prior
mission boundary.

**Deliberately not in this milestone:** any journal table, record method or
schema for `MarketThesis`; any change to `orion.reflection`; any frontend change
(the wire is additive and every existing key is unchanged, so no client is
obliged to move); multi-timeframe fetching; news; telemetry; and anything
resembling live or broker execution.

**Files added:** `tests/interface/test_specialist_surface.py` (18 tests).
**Files modified:** `src/orion/interface/{schemas,service}.py`,
`src/orion/cli.py`, `tests/interface/test_analysis_turn.py`,
`tests/test_cli.py`. No frontend file, no `orion.analysts` file, no
`orion.orchestrator` file, no `orion.memory`/`orion.paper`/`orion.risk` file and
no dependency manifest was touched, and no dependency was added.

**Verification:** `pytest` 2054 passed (2031 before), `ruff check .`, `ruff
format --check .` (199 files) and `mypy src --strict` (87 files) all clean. The
interface security and every import-hygiene suite pass unchanged. Proven rather
than claimed: a real HTTP turn returns five technical findings with their
computed observations and registry-authored interpretations, six unevaluated
concepts with reasons, and a thesis with its canonical summary — while the same
payload contains no 64-character fingerprint and no vendor name, and the turn
writes zero bytes to an isolated working directory.

## 2026-09-02 — Mission 8 v2.2: `analysis_id` names an analysis, the thesis is persisted, and reflection reads it back

**THIS ENTRY SUPERSEDES v2.1's PHASE B REFUSAL.** That entry refused to persist
a `MarketThesis` and named the two things that would unblock it: "a stable
identity for a thesis within the decision chain that does not point *at* the
decision", and "a home for the `MarketThesis` *schema* that both `orion.paper`
and `orion.memory` may already see". Both now exist, so the record is written.
Nothing in the earlier entry was edited; the refusal was correct for what
existed when it was made, and this is what changed underneath it.

**BLOCKER 1 — `analysis_id` NOW NAMES AN ANALYSIS, NOT A BAR, AND THAT IS THE
CENTRAL DECISION OF THIS MILESTONE.** `build_analysis_id` minted
`analysis-{symbol}-{timeframe}-{as_of}`, deriving the id from the evidence's
last bar rather than the wall clock. v2.1 demonstrated the cost: a cron at :05
and again at :35 produces two proposals, two verdicts and two theses under one
identifier, so a record found by that key cannot say which chain it belongs to.
The id now also carries `analyzed_at` — the same `now` already stamped on
`ProposedDecision.decided_at`, `RiskVerdict.reviewed_at` and
`MarketThesis.formed_at` — so one id covers exactly one chain.

**The alternatives were considered and rejected for reasons, not by taste.**
`uuid4()` was refused for the third time in this codebase: an identifier that
changes on every call makes a journaled record unreproducible from its own
inputs, and `run_backtest` and this module both derive theirs deliberately.
Putting a `decision_fingerprint` on the thesis was refused again, and for the
reason v2.1 gave — it would couple advisory evidence to the authority that
reviewed it, in the one direction that must stay closed. **Folding
`evidence_fingerprint` into the id was refused too, and this is the subtler
one**: the fingerprint is the *market-evidence* identity, it is content-
addressed, and it is what proves a finding describes the same bars a proposal
was reasoned from. Two analyses of one bar still share it — they looked at
identical bytes, which is true and must stay expressible. An id that carried
both would give one name to two different questions ("which market state" and
"which run over it"), and the second is the one a journal lookup asks. So the
two identities stay separate and each keeps its own meaning.

**What was given up is stated rather than glossed.** The old docstring promised
that "re-asking the same question before a new bar closes yields the same id".
That promise is gone on purpose: re-asking *is* a second analysis, and it now
says so. Determinism is unchanged — every component is still an input the caller
already holds, so the same bar analysed at the same instant yields the same id,
and `test_is_deterministic` passes untouched. A collision now requires two
analyses of one bar at the identical microsecond.

**THAT RESIDUAL COLLISION IS WHY NOTHING FAIL-CLOSED WAS RELAXED.** It would
have been tempting to treat uniqueness as guaranteed and simplify the readers.
Instead: the `market_thesis.analysis_id` column stays **not unique**, the list
methods still return tuples, and `orion.reflection._find_thesis` still raises on
more than one match. The journal is append-only and holds rows minted under the
older rule — a UNIQUE constraint would make an existing database fail to *open*
rather than fail closed on *read*, which is a worse trade for an audit store.
Uniqueness removes the ordinary ambiguity; it does not replace the check that
catches the extraordinary one.

**AND THE AMBIGUITY IS NEVER RESOLVED BY DROPPING DATA.** Returning `None` when
two theses share an id was rejected explicitly: it would hide that specialist
evidence exists, and quietly picking one would attach a reading to a trade it
may not describe. The trade lands in `ReflectionBatch.unreflectable_fingerprints`
— *named*, never silently omitted — and every other trade in the batch still
reflects, which is the contract a missing verdict already has.
`_find_opening_fee` refuses to guess between duplicate fills for the same
reason.

**BLOCKER 2 — THE SCHEMA MOVED, THE WALL DID NOT.** `orion.thesis` is a
schema-only package importing `orion.analysis.structure`, `orion.data.models`
and `orion.provenance` and nothing else — no analyst, engine, store, provider or
orchestrator. It exists because `orion.paper` journals a thesis, `orion.memory`
stores one and `orion.reflection` reads one back, and all three are forbidden to
import `orion.analysts`; that ban is the single mechanism keeping specialist
output away from every decision path, so the schema moved rather than the wall.
`orion.analysts.synthesis` remains the only thing that *builds* a thesis,
exactly as `orion.risk.guardian` is the only thing that builds a `RiskVerdict`
while `orion.risk.schemas` holds its shape. The vocabulary enums moved with it
and are re-exported from their original modules, so every existing import path
still resolves and the JSON schema the Macro Analyst sends to Claude is
byte-identical.

**THE WRITER IS THE ONE THAT OWNS THE ORDERING, AND IT IS PROVABLY BLIND.**
`orion.paper.session._persist` writes the thesis, so Mission 5's Finding A
survives intact: the account is saved first, then the journal, and a failed save
journals nothing at all. The thesis goes in **first** of the chain because it
references nothing, so no prefix of an interrupted write can leave a stored
record pointing at a fingerprint the journal does not hold. Mission 5's Finding
B is intact too — still one production writer, and no second one in `orion.cli`.
**`orion.paper.session` may hold a `MarketThesis` and may not look inside one**:
`test_the_thesis_is_carried_and_never_read` walks that module's AST and fails on
any attribute access into the value, spelled `thesis.anything` or
`chain.thesis.anything`, and also asserts the name is really there so the
guarantee cannot become vacuous. The module docstring has claimed that test
since the code was written; until now it did not exist.

**REFLECTION RECOVERS IT BY THE SIBLING KEY AND CONCLUDES NOTHING FROM IT.**
`TradeReflection.thesis` is optional enrichment in exactly the sense
`opening_fee` is: `reflect_on_closed_position` computes every
prediction-versus-outcome fact from the decision, the verdict and the closed
position, then attaches the thesis found under the verdict's own `analysis_id`.
A test serialises a reflection with and without one and asserts every other
field is identical. A trade opened before this existed reflects perfectly well
without one, permanently.

**THE THESIS REACHES A READER, WHICH IS THE POINT OF STORING IT.** `orion
reflect` prints the alignment, the technical bias and condition, the macro
condition when there is one, each agreement and conflict, and the thesis's own
canonical `summary` — between the prediction and the outcome, with no line drawn
between them. Every string is the record's own: `MarketThesis` validates that
`summary` is the canonical sentence for its alignment and that each
`ThesisPoint.detail` is the committed one, so the renderer authors no market
commentary, the same discipline `render_specialists` follows. Absence is a row
of its own (`no thesis was journaled for this trade`) rather than a blank a
reader could mistake for "the specialists had no opinion". **`orion.cli` still
does not import `orion.thesis`** — it reads fields off the `TradeReflection` it
already holds, the same way it reads `report.thesis.thesis` — so the package's
importer set is unchanged at exactly five.

**THE REPORT NOW VALIDATES THE THIRD MEMBER OF THE CHAIN.** `AnalysisReport`
checked `decision.analysis_id` and `risk.verdict.analysis_id` against its own
and did not check the thesis. It does now. A thesis points at nothing, so that
id is the only thing tying it to the analysis it was formed for, and `orion.cli`
takes it straight off the report and hands it to the journal: an unchecked
mismatch would file advisory evidence that is either invisible to reflection or
findable from a trade it was never formed beside.

**NOTHING ABOUT THE RISK GUARDIAN'S AUTHORITY MOVED.** `orion.decision` and
`orion.risk` still cannot import `orion.thesis` or `orion.analysts` — asserted
positively by name, not merely by absence. The thesis is still computed *after*
the decision and the verdict exist, still has no field capable of expressing an
action, direction-to-trade, quantity, entry, stop, target or approval, and is
still journaled only behind an APPROVED verdict that produced a fill: a veto
writes no chain and therefore no thesis, because advisory evidence is not a
reason to record a trade that never happened. Journal idempotency is unchanged
and now exercised for the seventh record through the real cycle — replaying an
identical thesis returns the same fingerprint and stores one row.

**Deliberately not in this milestone:** the six other Mission 8 roles; the News
Analyst (still blocked on a data source); any HTTP or frontend surface for the
reflected thesis — `orion.interface` is byte-identical and no frontend file was
touched; any reflection endpoint at all; multi-timeframe fetching; any UNIQUE
constraint or migration on the journal; any judgement, score or aggregate over a
thesis and an outcome; telemetry; and anything resembling live or broker
execution. No dependency was added.

**Files changed.** `src/orion/orchestrator/analysis.py` (the id, and its call
site), `src/orion/orchestrator/schemas.py` (the report validator),
`src/orion/cli.py` (`_thesis_rows`, `NO_THESIS_RECORDED`, `render_reflections`),
`src/orion/memory/journal.py` and `src/orion/memory/schema.py` (why the key is
not unique), `src/orion/reflection/analysis.py` and `src/orion/thesis/schemas.py`
(why the refusal stays, and what the two identities now mean), `README.md`.
Tests: `tests/paper/test_session.py` (the AST guarantee, nine persistence
tests, `record_thesis` on the recording double), `tests/paper/conftest.py` and
`tests/reflection/conftest.py` (an `analysis_id` and theses in the builders),
`tests/reflection/test_analysis.py` (six), `tests/reflection/test_batch.py`
(three), `tests/orchestrator/test_analysis.py` (the identity rule, restated as
three tests), `tests/orchestrator/test_schemas.py` (two),
`tests/test_cli_reflect.py` (seven, plus a thesis through `seed_history`),
`tests/thesis/builders.py` and `tests/memory/test_journal_thesis.py` (the new id
shape, and why the store still tolerates a collision). No frontend file, no
`orion.interface` file, no `orion.analysts` file, no `orion.paper`/`orion.risk`/
`orion.decision` source file and no dependency manifest was touched.

## 2026-09-03 — Mission 9 Phase 1: resolving a position is separated from analysing the market

**The problem, stated exactly.** `orion.paper.session.run_cycle` had two
branches. The first resolved an open position against a price; the second
considered a new one. Only the second needed an analysis — but both sat behind
one function, reachable only through `orion paper`, which runs
`AnalysisService.run` before it calls `run_cycle` at all. So checking whether a
stop-loss had been reached required a market-data request, `build_evidence`, a
**Claude call**, a `ProposedDecision` and a `RiskVerdict`, and the branch doing
the checking read none of them. Three consequences, all real: a deployment
without `ANTHROPIC_API_KEY` could not honour a stop it had already committed to;
an Anthropic outage or rate limit disabled exit handling; and an open position
was checked only when a human typed a command, because the only thing that could
check it was the analysis command.

**The decision: extract the branch, narrow its input, and give it a verb.**
`resolve_position` is that first branch, exported from the same module.
`resolve_stop_take_profit` now takes an `ObservedPrice` — the two fields it
actually reads — instead of an `Evidence`. `orion resolve SYMBOL` fetches one
price and calls it. Nothing about what a resolution *does* changed; what changed
is what must happen before one can.

**The resolver was asking for far more than it used.** It read exactly
`evidence.symbol` and `evidence.last_price`. `Evidence` additionally carries a
timeframe, a bar count, an `IndicatorSnapshot` and a `StructureSnapshot`, none of
which it touched. The precedent for the narrower shape was already in the
repository: `orion.backtest.execution.resolve_stop_take_profit_ohlc` has always
taken `(portfolio, bar, policy, now)` and never an `Evidence`, because the
backtest engine had no reason to build one for a resolution either.

**`ObservedPrice` is a Protocol, so no existing caller changed.** It declares
`symbol` and `last_price` as read-only properties, and `Evidence` satisfies it
structurally. Every pre-Mission-9 call site — `run_cycle`, and the twenty-odd in
`tests/paper/`, `tests/memory/` and `tests/reflection/` that drive real closes
through the real engine — compiles and passes untouched. The idiom is the one
`JournalWriter` in the same package already uses: restate a *surface* so two
unrelated types can satisfy it without either importing the other. Properties
rather than plain annotations, so a frozen model satisfies it as readily as a
mutable one. `as_of` is deliberately **not** a member: the resolver stamps a
close with the caller-supplied `now`, and declaring a timestamp would claim a
dependency this function does not have.

**`PriceObservation` is the concrete value type, and it lives in
`orion.data.models`.** A symbol, a last price, and the bar timestamp the price
was true at — frozen, `extra="forbid"`, positive price, timezone-aware `as_of`:
exactly the discipline every other model in that module already has. It sits
beside `OHLCVBar`/`MarketSnapshot` because it is a market-data shape, not a paper
one, and that module is a leaf importing nothing from the engines. It carries
`as_of` even though the resolver does not read it: a price without a time is not
evidence of anything, the CLI prints it, and that limit is documented rather than
left for a reader to discover. It must never grow an indicator — the moment it
can answer "what is the trend", something will reason from it, and reasoning is
`Evidence`'s job.

**EXIT NEEDS NO NEW AUTHORITY, AND NONE WAS ADDED. THIS IS THE LOAD-BEARING
CLAIM OF THE MISSION.** A stop-loss and a take-profit are not decisions taken at
resolution time. They were proposed by the model, narrowed by the Decision
Engine, approved by `orion.risk.guardian.review`, and stamped onto the `Position`
at the moment it was opened — `Position.stop_loss`, `Position.take_profit`,
`Position.opened_by_verdict_fingerprint`. Reaching one is the *execution of a
permission already granted*, not a request for a new one — the same way a real
broker's resting stop order does not re-ask permission to fill.
`orion.paper.execution`'s module docstring has said exactly this since Mission 4;
Mission 9 relies on that rather than inventing it. Concretely, therefore: no
control was added to `orion.risk`, no `RiskControlName` member, no `ExitPolicy`,
no `ExitAuthority`, no `CloseReason` member, and **no file under
`src/orion/risk/` was modified at all**. `CloseReason` still has exactly two
members, so a close nobody approved remains unrepresentable. Had exit genuinely
required new authority, the correct response would have been to build it in
`orion.risk` — it does not, and building one anyway would have created a second
authority able to diverge from the Guardian, which is precisely the failure
`orion.reflection` already refuses to become.

**`resolve_position` returns `CycleResult | None`, and `None` is not an
outcome.** A flat account has nothing to resolve. Reporting `HOLDING` would make
that outcome ambiguous between "a position is open and neither level was
reached" and "there is no position at all", and adding a `CycleOutcome` member
for it would widen an enum every renderer and exit-code table reads. `None` is
the not-applicable idiom `orion.decision.rules` and `orion.risk.controls`
already use. `run_cycle` falls through to its entry branch on `None`; the CLI
prints one sentence and exits 0.

**It takes a `PortfolioRecord`, not a `PortfolioStore`, to read from.** The
record carries the revision the compare-and-swap needs, so `run_cycle` still
loads the account exactly once and the extraction adds no second read. The
load-failure branch stays with the caller that already had it.

**Persistence, ordering and the sole-writer guarantee are untouched.** The
account is saved before the journal is written, always (Mission 5's Finding A);
a close writes no `AuditChain`, because the records that authorised the position
were journaled by the cycle that opened it; journal writes remain
content-addressed and idempotent, so the crash window is still recoverable by
replay. The extraction stays **inside `paper/session.py`** specifically so
`tests/paper/test_session.py::test_nothing_else_in_src_calls_record_fill_or_record_closed_position`
— which asserts `callers == {"paper/session.py"}` — keeps passing unweakened. A
second module journaling closes would have been a second production writer,
which Mission 5's Finding B says must not exist.

**HOLDING still writes nothing, and that matters more than it used to.** The
marked-to-market projection is computed and returned but never saved. At one
invocation a day that was tidiness; at monitoring frequency it is the difference
between an account that records decisions and one that records that somebody
looked. `tests/paper/test_resolution.py` asserts the *revision* is unchanged
across five consecutive holds, not merely that `save` was not called.

**The CLI verb is `orion resolve SYMBOL`, a bare verb.** Three of the four
existing top-level commands (`analyze`, `paper`, `reflect`) are bare verbs;
`account` is a noun group because it has two actions. Resolution has one action,
and a noun group containing a single verb is structure built ahead of need,
which this codebase's own precedents refuse. It fetches one bar, validates it
with `validate_snapshot` against the same `RiskPolicy.max_staleness_bars` the
analysis path uses (a month-old bar must never resolve a stop; with one bar only
EMPTY and STALE can fire, the rest need two), builds a `PriceObservation` and
calls `resolve_position`. New exit code `EXIT_PRICE_UNAVAILABLE = 9`, distinct
from `EXIT_ANALYSIS_FAILED` because no analysis was attempted.

**One credential, and the wiring says so.**
`orion.orchestrator.wiring.market_data_available` tests only
`TWELVE_DATA_API_KEY`, and `build_market_data_provider` returns the bare
`MarketDataProvider` ABC with no reasoning provider, no policy and no analyst
around it. `NOT_CONFIGURED_FOR_PRICES_MESSAGE` names only the key this command
uses — telling a user to set `ANTHROPIC_API_KEY` for a command that never calls
a model would be a false statement in the one place a user is most likely to
believe it. This is a second construction of `TwelveDataProvider` in the same
file rather than a shared helper: the two callers want different things, and
collapsing them is exactly how a reasoning provider would find its way back onto
the resolution path. `tests/test_secret_handling.py`'s exact
`get_secret_value()` count moves 4 to 5 to record it.

**What is deliberately still absent.** No scheduler, no daemon, no loop, no
thread, no sleep, no retry, no broker, no MT5, no live execution —
`tests/paper/test_session.py::test_the_session_contains_no_sleep_retry_or_thread`
still passes. Mission 9 Phase 1 makes frequent resolution *possible and cheap*;
it schedules nothing, and scheduling remains a separate mission with its own
approval. Intrabar resolution is also still deferred: the forward path remains
last-price-only, so a price that travels through a level and back between two
invocations is not seen. Adopting `resolve_stop_take_profit_ohlc` on the forward
path changes fill semantics and deserves its own entry. Multi-symbol and
multi-position remain out of scope; `PortfolioState` is still one symbol and at
most one position.

**Tests.** 24 added, none weakened or deleted.
`tests/paper/test_resolution.py` (12): resolution with no reasoning provider,
stop and target both, the verdict fingerprint surviving a close performed with no
verdict in sight, a flat account returning `None`, symbol mismatch failing
closed, HOLDING leaving the revision untouched across five passes, a price sweep
across the whole geometry producing no `Fill`, an AST walk of
`resolve_position`'s own body asserting it calls no
`execute`/`record_fill`/`compute_position_size`/`review`/`decide`, the extracted
path journaling identically to `run_cycle` (same `ClosedPosition`, same
fingerprint, same writes, same resulting account), and `Evidence` still
satisfying `ObservedPrice`. `tests/test_cli_resolve.py` (12): every one runs with
`ANTHROPIC_API_KEY` deleted *and* `anthropic.Anthropic` monkeypatched to raise if
constructed, so "no model was called" is proven by the call being impossible;
plus one fetch of one bar on the configured timeframe, holding writing no
revision, a flat account never reopening, refusals that create no database and
spend no vendor credit before checking for an account, a provider error leaking
neither vendor nor key, and a stale price refusing rather than closing.

**Files changed.** `src/orion/data/models.py` (`PriceObservation`),
`src/orion/paper/execution.py` (`ObservedPrice`, the narrowed resolver signature
and its message), `src/orion/paper/session.py` (`resolve_position`, and
`run_cycle` delegating to it), `src/orion/orchestrator/wiring.py`
(`market_data_available`, `build_market_data_provider`), `src/orion/cli.py` (the
verb, `render_resolution`, `_run_resolve`, `_default_market_data_factory`,
`MarketDataFactory`, one exit code and three messages), `README.md`. Tests:
`tests/paper/test_resolution.py` and `tests/test_cli_resolve.py` (new),
`tests/paper/test_execution.py` (one `match=` string, for the renamed parameter),
`tests/test_cli.py` (the verb surface, the import allowlist, and a new ban on
reasoning imports in the CLI), `tests/test_cli_account.py` (the verb surface),
`tests/test_secret_handling.py` (the unwrap count). **No file under
`src/orion/risk/` was touched**, nor `orion.memory`, `orion.paper.store`,
`orion.decision`, `orion.reflection`, `orion.backtest`, `orion.interface`, any
frontend file, or any dependency manifest.

## 2026-09-03 — Mission 9 Phase 2: exits resolve on the bar range, and gaps fill honestly

**The hole Phase 1 left.** Phase 1 made resolution cheap — no model call, one
credential — but it still asked the same question it always had: *is the last
price beyond the level?* A price that crosses a stop and comes back before the
next observation leaves **no trace in any bar's close**, so a position that
genuinely stopped out reported `HOLDING` and stayed open. Making resolution
frequent did not fix that; it only narrowed the window in which it could happen.
The information was never missing — `OHLCVBar` has carried `high` and `low`
since Mission 0, and `orion resolve` was fetching them and reading only `close`.

**The decision: detect on the bar range, scan every bar since the entry, and
report how much of that window was actually seen.**
`resolve_stop_take_profit_bars` is a new sibling in `orion.paper.execution`;
`resolve_stop_take_profit` is untouched and still what `run_cycle` calls.

**WHY OHLC DETECTION IS NOT LOOK-AHEAD.** Mission 4's B6 chose last-price-only
resolution and justified it as "correct for live trading (no visibility into the
future)". That reasoning is about *future* bars. A bar that has already closed is
the past: its low is a price that genuinely traded, and refusing to look at it
does not make the system more conservative, it makes it blind. The detection rule
is the Mission 6 one, reused verbatim and cited rather than re-decided —
including M6-Q2's stop-first tie-break when a single bar reaches both levels,
because OHLC still cannot say which came first.

**WHY FORWARD FILLS DIFFER FROM BACKTEST FILLS — THE LOAD-BEARING DIVERGENCE.**
`orion.backtest.execution.resolve_stop_take_profit_ohlc` always fills at the
level: a bar with `low=2340` against a stop at `2350` books the exit at `2350`,
pinned by `tests/backtest/test_execution.py`. That models a resting order and is
defensible for replaying history. Adopting it forward would have been a silent
regression in honesty — on every adverse gap the paper account would report a
smaller loss than the market handed out, in a module whose own fill helpers are
documented as never being more favourable than the real cost of trading. So the
backtest keeps its rule, unmodified, and the forward path gets its own.

**THE WORST-OF GAP RULE.** Fill at the level, **unless the bar had already
opened through it**, in which case fill at the open. Stated once and symmetric
in all four cases: if `bar.open` on its own already satisfies the trigger
condition, the level was never available after the bar began.

    LONG  stop    triggers on price <= stop_loss    -> an open below it wins
    LONG  target  triggers on price >= take_profit  -> an open above it wins
    SHORT stop    triggers on price >= stop_loss    -> an open above it wins
    SHORT target  triggers on price <= take_profit  -> an open below it wins

Deterministic, computed only from `open` and the position's own levels, using no
data the bar does not already carry and inventing no vendor behaviour. Spread,
slippage and fees still apply on top through the existing helper, so a gapped
exit is never better than a clean one. `_close_at` was extracted so both forward
resolvers share one copy of the cash-conservation arithmetic rather than growing
a third.

**BARS AT OR BEFORE `Position.opened_at` ARE EXCLUDED.** The bar the position was
opened from had already traded its range before the entry existed; honouring its
extremes would close a position on prices that predate it. Excluding the whole
bar containing `opened_at` also discards the part of it after entry — a missed
exit, never an invented one, and the conservative direction. `orion.backtest.
engine` reaches the same exclusion positionally by resolving from the bar after
the entry; forward has no loop index, so the boundary is a timestamp comparison
and is documented as a convention rather than a derivation.

**WHY `ClosedPosition` WAS NOT TOUCHED.** It is journaled and content-addressed:
adding a triggering-bar field would move the fingerprint of every future record
and split the journal into pre- and post-Phase-2 shapes for a value nothing reads
back. The bar timestamp is exposed on `CycleResult.triggering_bar_at` and printed
by `orion resolve` instead — a container that is never journaled or hashed.
`closed_at` remains the caller-supplied `now`, exactly as before; a close is
still stamped when it was *recorded*, and the bar that caused it is provenance
for a reader, not a second timestamp competing to be authoritative.

**WHY COVERAGE LIVES OUTSIDE `CycleOutcome`.** `HOLDING` says "neither stop nor
target was reached". That sentence is only true if every bar since the entry was
examined, and a window can fall short two ways: it may not reach back to the
entry, or it may be missing bars inside itself. Folding those into `CycleOutcome`
would either multiply an enum that every renderer and exit-code table reads, or —
worse — let an unexamined window pass as a clean hold. So `ResolutionCoverage`
(`COMPLETE` / `TRUNCATED` / `GAPPED`) rides alongside the outcome on
`CycleResult`, is `None` on every path that resolved no window (all of
`run_cycle`), and is printed unconditionally by `render_resolution` so the happy
case cannot be told apart from the blind one by omission. Three states rather
than a bool, for the reason `orion.risk.schemas.ControlStatus` already gives for
its own three: a truncated window is fixed by asking for more bars, a gapped one
is the vendor's answer being short and cannot be.

**A GAP DOWNGRADES THE CLAIM; IT DOES NOT REFUSE.** `orion resolve` still runs
`validate_snapshot` and still refuses outright on EMPTY, STALE, NON_MONOTONIC and
DUPLICATE_TIMESTAMP — data it cannot trust at all. `GAP` alone is treated
differently, and that asymmetry is the whole design: a gap means *some bars are
missing*, which is exactly what `GAPPED` reports, and refusing on it would leave
a real stop unhonoured every time the window spans a market weekend. Fail-closed
here means failing the *claim*, not the exit.

**THE VENDOR-GAP QUESTION IS OPEN, AND THE DESIGN DOES NOT DEPEND ON IT.** Every
Twelve Data test in this repository uses synthetic, contiguous two-bar payloads,
so nothing here establishes whether real XAUUSD H1 output actually contains
weekend gaps; determining it would need a live network call, which no test adds.
The uncertainty is recorded rather than resolved, and the behaviour is correct
either way: if such gaps occur, resolution proceeds and says `GAPPED`; if they do
not, nothing changes.

**THE FORMING BAR, WITHOUT INVENTING A FLAG.** The provider exposes no
completion marker and none was fabricated. The asymmetry that follows is stated
in the code: a hit seen in the newest bar closes the position, because those
prices traded; the *absence* of a hit in a possibly-forming bar proves nothing
about the rest of it. `COMPLETE` therefore means "every bar the window should
contain was present and examined", never "nothing further can happen", and the
rendered text says so.

**THE FETCH WINDOW IS DERIVED, THEN CAPPED.** `orion resolve` now reads the
account *before* asking the vendor — the window comes from `opened_at`, and an
account with nothing open costs zero vendor requests instead of one.
`resolution_window_bars` requests one bar per elapsed interval plus a two-bar
margin (the margin is what lets coverage *prove* the window reached back rather
than assume it), capped at `MAX_RESOLUTION_BARS = 500` so a position left open
for months cannot turn one command into an unbounded request. A window that hits
the cap reports `TRUNCATED`; it is never passed off as complete.

**`orion paper` IS THE COMPATIBILITY PATH AND IS UNCHANGED.** `run_cycle` keeps
its signature and its last-price resolver, and its results carry no coverage and
no triggering bar. Making it intrabar-aware would mean widening `AnalysisReport`
to carry bars — `Evidence` has no `high`/`low` — which is a change to the
orchestrator contract for no gain the new verb does not already provide. The two
forward paths therefore differ deliberately: `orion paper` opens positions and
resolves on a last price, `orion resolve` resolves on the bar range. Both share
one persistence branch, so there is still exactly one way a close is written.

**NO NEW EXIT AUTHORITY EXISTS, AND THAT IS UNCHANGED FROM PHASE 1.** The stop
and target honoured here were proposed by the model, narrowed by the Decision
Engine and approved by `orion.risk.guardian.review` when the position opened;
reaching one executes a permission already granted. Phase 2 changes only *how a
crossing is detected* and *what price it fills at* — never who may close. No
control was added to `orion.risk`, no `RiskControlName` member, no `ExitPolicy`,
no `ExitAuthority`, no `CloseReason` member, no `JournalWriter` method, and **no
file under `src/orion/risk/` or `src/orion/backtest/` was modified at all**.

**Preserved, and asserted.** Account-before-journal ordering; no second
`AuditChain` on a close; content-addressed idempotent journal writes; immutable
portfolio state; `HOLDING` creating no `PortfolioStore` revision (now across
repeated bar-path passes); `paper/session.py` remaining the sole production
caller of `record_fill`/`record_closed_position`; no network imports in
`paper/session.py`; no sleep, retry, thread or loop anywhere. All four structural
guards in `tests/paper/test_session.py` pass unmodified.

**Tests.** 54 added, none weakened or deleted.
`tests/paper/test_resolution_bars.py` (30): the four directional cases on
`[low, high]`; both-touched resolving as a stop for LONG and SHORT; a bar range
that touches nothing; the same bar holding on last price and closing on its
range; all four gap-through cases filling at the open; a non-gapped bar still
filling at the level; a gapped stop booking a worse loss than the level would;
spread and slippage still worsening the fill on top; first-triggering-bar wins
and later bars ignored; quiet bars before a hit; the entry bar excluded;
`eligible_bars` boundaries; a window entirely before the entry leaving the
account untouched; symbol mismatch raising; a flat account closing nothing; no
bar ever producing a fill; and seven `window_coverage` cases including a hole
*before* the entry correctly not counting as gapped.
`tests/paper/test_resolution.py` (+13): the bar path through `resolve_position` —
closing where a last price holds, account-then-journal ordering, no fill, the
triggering bar reported but not journaled, repeated holding creating no revision,
symbol mismatch failing closed, all three coverage states surfacing on the
result, an imperfect window still closing a real hit, the last-price path
claiming no coverage, and `run_cycle` results carrying neither field.
`tests/test_cli_resolve.py` (+11, fixtures reworked): the derived window sized
from `opened_at` and bounded by the cap, wick-through-target and
wick-through-stop closing from the command line, the triggering bar rendered, a
gap through the stop filling at the open as read back *from the journal*, all
three coverage states rendered with `INCOMPLETE` spelled out, a gapped window
still closing a real hit, and a flat account now spending no vendor request at
all. Every one still runs with `ANTHROPIC_API_KEY` deleted and
`anthropic.Anthropic` monkeypatched to raise if constructed.

**Files changed.** `src/orion/paper/execution.py` (`resolve_stop_take_profit_bars`,
`eligible_bars`, `_levels_reached`, `_trigger_price`, and `_close_at` extracted
from the existing resolver), `src/orion/paper/session.py` (`ResolutionCoverage`,
`INCOMPLETE_COVERAGE`, `window_coverage`, two `CycleResult` slots and a
`coverage_is_incomplete` property, `resolve_position`'s optional `bars`),
`src/orion/cli.py` (`resolution_window_bars`, `MAX_RESOLUTION_BARS`,
`_COVERAGE_SUMMARY`, `_COVERAGE_ONLY_DATA_ISSUES`, account-before-fetch
reordering, coverage and triggering-bar rendering), `README.md`. Tests:
`tests/paper/test_resolution_bars.py` (new), `tests/paper/test_resolution.py`,
`tests/test_cli_resolve.py`. **No file under `src/orion/risk/` or
`src/orion/backtest/` was touched**, nor `orion.memory`, `orion.paper.schemas`,
`orion.paper.store`, `orion.decision`, `orion.reflection`, `orion.orchestrator`,
any frontend file, or any dependency manifest.

## 2026-09-03 — Mission 9 Phase 3: a supervised monitor, and the one loop ORION has

**What Phases 1 and 2 left.** Resolution became cheap (no model, one credential)
and then accurate (intrabar, coverage-reported). It was still only ever run when
somebody typed `orion resolve`. A stop-loss checked by hand is not a stop-loss,
and the gap between two hand-typed invocations is exactly the interval Phase 2
worked to make visible. Phase 3 closes it with a foreground supervisor and
nothing else.

**The decision: one new package, one new verb, one loop.**
`src/orion/monitor/` holds `iteration.py` (one stateless pass) and
`supervisor.py` (the loop). `orion monitor SYMBOL --interval N` enters it.
`orion resolve` is unchanged in behaviour, output and exit codes, and now
delegates to the same pass.

**THE ITERATION WAS EXTRACTED, NOT REWRITTEN.** Phase 2 put the sequence — read
the account, derive a window, fetch it, validate it, resolve — inside
`orion.cli._run_resolve`, welded to `print` and exit codes. A second copy on a
timer would have drifted from the first within a mission. `resolve_once` is that
sequence with the printing removed and every collaborator taken as an argument,
and both verbs call it. There is one implementation of what a monitoring pass
does, and `tests/monitor/test_iteration.py` drives it directly.

**THE SUPERVISOR KNOWS NOTHING ABOUT TRADING, AND THAT IS THE SAFETY ARGUMENT.**
`run_monitor` takes `Callable[[], bool]` — `True` for progress, `False` for a
failed pass — and that is its entire contract. It never sees a `PortfolioStore`,
a `CycleResult`, a price or a position, so "stateless between iterations" is a
property of the type signature rather than a discipline someone must keep. A
test walks its AST and asserts the words `store`, `journal`, `record`,
`portfolio`, `revision`, `price` and `cycle` appear nowhere in it.

**EVERY COLLABORATOR IS BUILT AND DESTROYED PER PASS.** `_one_iteration` opens
the engine, store and journal, runs the pass, and disposes them; the vendor
client is constructed inside the pass and dies with it. No account state, no
revision, no connection and no HTTP client crosses an iteration boundary, so a
crash costs at most the pass in flight and a restart simply performs the next
one. The `PortfolioStore` is the only thing that remembers anything, which is
what makes repetition safe.

**SETTINGS ARE RE-READ EVERY PASS, NEVER CAPTURED.**
`RiskPolicy.from_settings` documents that a fresh, uncached `get_settings()` per
cycle is what gives an operator kill-switch response with no restart. A monitor
that read `.env` once at startup would have been the first thing in ORION
requiring a restart to respect it — which matters more over a process that runs
for days, not less.

**NO RETRY, AND THE DIFFERENCE IS WHAT MAKES THE CEILING WORK.** A failed pass is
never attempted again; the next scheduled pass does fresh work from the store.
That is what lets a single counter distinguish two very different things without
inspecting one exception: **a lost race against another writer heals itself** —
the position is simply gone, and the next pass reports nothing to resolve —
while a broken disk or a dead vendor does not. So consecutive failures are the
signal, any success clears the count, and three in a row stop the loop with the
existing `EXIT_CYCLE_FAILED`. No new `CycleOutcome`, no new exit code.

**SHUTDOWN NEVER INTERRUPTS A PASS.** `_persist` writes the account and then the
journal, and a process torn down between those two leaves a real trade with no
audit row. So SIGINT and SIGTERM set a flag rather than raising: Python's default
SIGINT raises `KeyboardInterrupt` at whatever line is executing, which could be
exactly there. The flag is read only between passes and during the wait, the
wait is a `threading.Event` so a signal wakes it immediately instead of after a
full interval, and every exit path is taken *before* sleeping — a stopping loop
never waits first. No thread is started anywhere; the `Event` is used solely for
its interruptible wait.

**THE INTERVAL IS REQUIRED AND HAS NO DEFAULT.** A cadence is a deployment
assumption, and a shipped default becomes the number everybody runs without
having chosen it — the same reasoning that makes `orion account open --balance`
required. `nan` and `inf` both parse as floats and neither is a duration, so
both are refused along with anything under `MIN_MONITOR_INTERVAL_SECONDS = 60`,
which on the default 1h chart is already sixty checks per bar. Nothing was added
to `Settings`: two surfaces for one decision would immediately raise the question
of which wins.

**PREFLIGHT RUNS BEFORE THE LOOP.** An unknown symbol, a missing account and a
missing market-data credential cannot heal by waiting. Discovering one on the
first pass and then sleeping an hour would be the worst available behaviour, so
all three are checked up front, exit with the codes `orion resolve` already
returns, and reach no vendor and no sleep.

**A FLAT ACCOUNT KEEPS THE MONITOR ALIVE AND COSTS NOTHING.** Phase 2 made the
pass read the account before the vendor, so an account with nothing open builds
no HTTP client and makes no request. That is what makes a short interval
defensible against a rate-limited free tier, and it is why "nothing to resolve"
counts as progress rather than a failure.

**OVERLAPPING MONITORS ARE PERMITTED AND UNGUARDED, DELIBERATELY.**
`SqlitePortfolioStore.save` compares the revision inside its transaction and
inserts under a primary-key constraint, so a stale writer raises
`PortfolioStoreConcurrencyError`, writes nothing and overwrites nothing. CAS is
the concurrency authority and needs no help. A lockfile would add stale-lock
recovery — its own failure mode — for no safety gain, so none was added. Two
monitors on one account is wasteful (double vendor spend), not unsafe, and is
documented as unsupported rather than blocked.

**`JOURNAL_UNAVAILABLE` IS NOT SELF-HEALING, AND NOTHING PRETENDS OTHERWISE.**
If the account write succeeds and the journal write then fails, the trade is real
and its audit row is missing (Mission 5's Finding A, unchanged). The next pass
cannot repair it: being stateless, it has lost the `ClosedPosition` the replay
would need, and the position is already gone from the account, so the pass
correctly reports nothing to resolve. A monitor makes this more likely to occur
than a hand-typed command did, purely by running more often. It is recorded here
rather than papered over with an invented recovery.

**NO OBSERVABILITY WAS ADDED.** No logging framework, no metrics, no health
endpoint, no alerting, no systemd unit, no container. `orion.cli` states it
imports no logging framework and that is still true. Each pass prints the same
human rendering `orion resolve` prints, to stdout, which whatever supervises the
process already captures. Mission 13 owns this and would likely replace anything
bespoke added now.

**WHAT THE LOOP STILL CANNOT DO.** No entry analysis, no proposal, no Risk
Guardian call, no `execute()`, no model. `tests/monitor/test_import_hygiene.py`
asserts `orion.monitor` imports none of `anthropic`, `orion.reasoning`,
`orion.analysts`, `orion.risk.guardian`, `orion.risk.controls`,
`orion.decision`, `orion.orchestrator`, `orion.config` or `orion.paper.execution`
— that last one because `execute()` is what opens positions. Every CLI monitor
test runs with `ANTHROPIC_API_KEY` deleted and `anthropic.Anthropic` replaced by
something that raises.

**Tests.** 65 added, none weakened or deleted.
`tests/monitor/test_supervisor.py` (17): exact iteration counts; a wait between
passes and none after the last; a single pass never waiting; the configured
interval passed through exactly; a flag set before the loop running zero passes;
a signal arriving mid-pass honoured only after it completes and without a wait;
shutdown during the wait; handler idempotence; the installed handler setting a
flag rather than raising; one failure not stopping; three consecutive failures
stopping; a success resetting the count; an unbroken run being required; an
exception propagating rather than being counted or swallowed; and the AST check
that no domain name appears in the loop. Not one of them sleeps.
`tests/monitor/test_iteration.py` (13): a wick through the stop closing where a
last price holds; a quiet window holding and writing no revision; coverage
surfacing as COMPLETE and TRUNCATED; the window derived from how long the
position has been open; a flat account building no client at all; unreadable
account, missing credential, vendor failure, stale data and symbol mismatch each
reported rather than raised; no fill ever written; and identical inputs producing
identical results down to the journal fingerprint.
`tests/monitor/test_import_hygiene.py` (7) and `tests/test_cli_monitor.py` (28):
the verb, the required interval, ten rejected interval values, the exact minimum
accepted, preflight before any loop/vendor/sleep, a bounded run closing a
position, a flat account staying alive and spending nothing, three failures
exiting 7, each pass reading the account fresh, no Anthropic client ever
constructed, and `orion resolve` still doing one thing once.

**Files changed.** `src/orion/monitor/__init__.py`, `iteration.py`,
`supervisor.py` (new); `src/orion/cli.py` (the verb, `_monitor_interval`,
`_run_monitor`, `_resolvable_symbol`, `_one_iteration`, `_report_iteration`,
`_iteration_exit_code`, four messages, the corrected "no loop" docstring, and
`_run_resolve` reduced to preflight plus delegation); `tests/conftest.py` (the
`account_home` and `no_model` fixtures, shared by both Mission 9 verbs);
`tests/test_cli.py`, `tests/test_cli_account.py` (verb surface and import
allowlist); `tests/test_cli_resolve.py` (fixtures moved to conftest, window
helper imported from its new home); `README.md`. **No file under
`src/orion/risk/`, `src/orion/backtest/` or `src/orion/memory/` was touched**,
nor `orion.paper` (`run_cycle`, `resolve_position`, `CycleOutcome`,
`ClosedPosition`, `CloseReason` and the journal writer interfaces are all
exactly as Phase 2 left them), nor `orion.orchestrator` schemas, nor any
frontend file or dependency manifest.

---

## 2026-09-04 — Mission 6 Phase 2: session-aware gap validation, and the weekend that blocked every real dataset

**`orion.backtest` shipped complete, tested, and unable to run on a single real
week of gold.** `run_backtest` validates its whole fetched range once, up front,
through `orion.data.validation.validate_snapshot`, whose `gap_tolerance_bars`
defaults to `1.5`. XAUUSD closes for the weekend, so every real multi-week
XAUUSD series contains a ~49-hour hole between two entirely healthy bars —
wider than the tolerance at every timeframe. Verified against the real validator
before any code was written: a 120-bar H1 series spanning one weekend produces
`gap of 2 days, 1:00:00 between bar 119 and bar 120`, and `run_backtest` raises
`BacktestError` before the first reasoning call. Sixty-seven Mission 6 tests
passed and none of them caught it, because every fixture was a synthetic
contiguous ramp. This phase removes that blocker and nothing else.

**This is Mission 6, not Mission 10.** The engineering brief assigns Mission 10
to the ORION Interface and this log already carries six Mission 10 phase entries
for it; backtesting is Mission 6 in both documents. The work was originally
proposed under a "Mission 10" heading and renumbered before implementation, so
that a reader of this log never has to disambiguate two Mission 10s. The
continuation is numbered from the 2026-08-24 Mission 6 entry, which is Phase 1
and is not edited — this log is append-only.

**M6-Q7 (session gaps), resolved: a declared weekly closure, matched at both
boundaries.** Continuing the M6-Q1…Q5 numbering the original Mission 6 entry
established. The new `orion.data.sessions.TradingSession` is a frozen, closed
Pydantic model holding a recurring weekly close and open, each a
`(calendar.Day, time)` pair in UTC. `calendar.Day` is reused rather than
defining a parallel weekday enum: it is stdlib, it already agrees with
`datetime.weekday()`, and a second enum that merely happens to agree today is
the kind of drift `orion.provenance` exists to prevent. Its one method,
`is_expected_closure(previous, following, interval)`, returns True only when the
gap *begins* at the declared close and *ends* at the declared open, each within
one bar `interval`.

**The check became narrower, not looser — that is the whole design.** Three
properties, each with its own test:

1. **A mid-session gap of identical duration is still a `GAP`.** The same
   49-hour hole moved into the middle of the trading week is rejected. Duration
   alone forgives nothing; only boundary alignment does.
2. **A boundary-aligned gap that runs past the open is still a `GAP`.** A
   holiday-extended weekend matches the close and misses the open, and is
   flagged. Holidays are deliberately not modelled — a holiday calendar is
   external data that can be wrong or stale, and a wrong one silently
   suppresses genuinely missing bars, which is the failure this codebase
   refuses everywhere else. An unexpected closure should surface and be dealt
   with explicitly.
3. **A session never suppresses a second, unrelated gap.** A series containing
   one weekend and one genuine mid-week hole produces exactly one finding, and
   its `bar_index` names the real one.

Also rejected, each with a test: a gap matching only the open boundary, a gap
spanning two closures, and a gap beginning after the close on the close day.

**`gap_tolerance_bars` was not raised, and a test now stops it being raised
later.** Widening it was the obvious alternative and is the wrong one: it is a
single global figure consulted at every timeframe on both the live and backtest
paths, so a value loose enough to admit a weekend would blind the check to
genuinely missing bars everywhere. `tests/data/test_import_hygiene.py`'s
`test_no_caller_in_src_raises_the_gap_tolerance` AST-parses every
`validate_snapshot` call site in `src/` and fails if any passes
`gap_tolerance_bars`; a sibling test pins the `1.5` default itself. A future
edit that "fixes" a gap finding with a bigger number instead of a declared
session is exactly what those two tests exist to catch.

**`session=None` is the default, everywhere, and reproduces the previous
behaviour exactly.** The gap branch short-circuits on `session is not None`
before consulting anything, so the no-session path is byte-identical to what it
was. `test_session_none_is_byte_identical_to_omitting_it` compares
`validate_snapshot(...)` against `validate_snapshot(..., session=None)` across
gapped, clean and duplicate-timestamped inputs, and the pre-existing
`test_gapped_data_fails_closed_before_any_reasoning_call` passes unmodified. A
new engine test, `test_weekend_spanning_dataset_fails_closed_without_a_session`,
pins that a real weekend dataset *still* fails closed unless a session is
declared. There is no default session anywhere: `XAUUSD_WEEKLY` is data a caller
opts into, never a threshold this package applies on anyone's behalf — the same
posture `RiskPolicy`'s fail-closed defaults take. Staleness, emptiness, ordering
and duplicate detection are untouched by a session, and a test asserts all four
still fire with one supplied.

**`run_backtest` gained one keyword and forwards it, unexamined.**
`session: TradingSession | None = None` is passed straight to the single
up-front `validate_snapshot` call. The bar loop, `_window_for`'s no-look-ahead
slice, the branch order, the execution semantics and the determinism guarantees
are untouched — a session is consulted only while classifying data quality,
before the first bar is processed, which
`test_session_changes_nothing_on_a_dataset_that_was_already_valid` proves by
asserting a byte-identical `BacktestResult` with and without one on a contiguous
dataset. `tests/backtest/test_import_hygiene.py` was not edited: `orion.data`
was already an allowed import, so the package acquired no new dependency, and
that untouched file is the evidence the core was not reopened.

**Known limitation — DST, measured rather than assumed.** A `TradingSession` is
a fixed UTC boundary, and the real gold week tracks New York time, so the true
close and open move by an hour across US DST transitions. `XAUUSD_WEEKLY`'s
22:00 UTC matches US winter time; under summer time the real boundary is 21:00
UTC. Whether the one-interval tolerance absorbs that hour depends on the
timeframe and the vendor's bar grid, so it was checked against the shipped
constant rather than reasoned about: with bars on the UTC interval grid, a
winter-time weekend is accepted at M15, H1, H4 and D1, and a summer-time weekend
is accepted at **H4 and D1 but rejected at H1 and M15** — their tolerance is too
narrow to cover an hour. A summer-time weekend on H1 or M15 data therefore still
raises a `GAP` finding. That is fail-closed and noisy rather than silent and
wrong, which is the correct direction to err, so it ships as-is. `zoneinfo` was
not reached for: making the boundary DST-aware means depending on external
timezone rules to decide whether real bars are missing, and the right input for
that decision is real vendor data, which this repository does not have yet.

**`XAUUSD_WEEKLY`'s hours are conventional, not measured.** Friday 22:00 UTC to
Sunday 22:00 UTC is the standard gold/FX week — New York close to Sydney open —
quoted in UTC under US winter time. It is not derived from a fetched XAUUSD
series, because no such series exists in this repository: Phase 3 is what
acquires one. This is the same honesty `classify_volatility`'s thresholds and
`RiskPolicy`'s loss limits are already recorded with, and it is why the constant
is a caller's opt-in rather than a default: a caller whose real data disagrees
with this boundary should declare their own `TradingSession`, never widen the
tolerance.

**The live path has the same defect and is deliberately untouched.**
`orion.orchestrator.analysis` validates its fetched snapshot the same way at
`DEFAULT_OUTPUT_SIZE = 200`, and 200 H1 bars is roughly 1.7 gold trading weeks,
so a live `orion analyze XAUUSD` should be raising `AnalysisDataQualityError`
with a gap finding on H1, H4 and D1 today, and on M15 whenever the window
straddles a weekend. That is a prediction read off the code, **not an observed
failure** — no network call was made and no API key was used, so it is recorded
here as unverified. Fixing live behaviour on the strength of a guess is not
something this codebase does. The mechanism now exists and the fix is one
keyword argument; the next step is a single real `orion analyze XAUUSD` run to
confirm the prediction, after which it becomes its own small phase with its own
entry.

**Alternatives considered.** Raising `gap_tolerance_bars`, globally or per
timeframe (rejected — blinds the check to genuinely missing bars on both paths,
and is the specific thing the new hygiene test now prevents). Skipping
validation for backtests (rejected — blinds it completely, and the up-front
`validate_snapshot` call is what makes every per-cycle window trustworthy).
Modelling holidays (rejected — external data that can be wrong, silently
suppressing real findings). Deriving the boundary from `zoneinfo` (deferred —
needs real vendor data to justify, exactly as the volatility thresholds do).
Inferring the closure from the data itself (rejected outright — a system that
learns which of its own gaps are acceptable can no longer detect a missing bar).

**Deliberately not in this phase:** historical fetching, datasets or
fingerprinting; any strategy abstraction; sweeps, walk-forward or Monte Carlo;
any volatility or calibration change; any CLI or HTTP surface; the live-path fix
above; and anything touching `orion.paper`, the backtest execution semantics, a
fingerprint function, a journaled model, or the frontend.

**Files added:** `src/orion/data/sessions.py`,
`tests/data/{test_sessions.py, test_import_hygiene.py}` (23 tests).
**Files modified:** `src/orion/data/validation.py` (one parameter, one
short-circuited condition), `src/orion/backtest/engine.py` (one parameter,
forwarded), `tests/data/test_validation.py` and `tests/backtest/test_engine.py`
(11 tests added, none removed or weakened). No `orion.paper` file, no
`orion.analysts` file, no `orion.monitor` file, no frontend file and no
dependency manifest was touched, and no dependency was added.

**Verification:** `pytest` 2291 passed (2257 before), `ruff check .`, `ruff
format --check .` (220 files) and `mypy src --strict` (93 files) all clean.
`orion.data.sessions` is at 100% line coverage and `orion.backtest` holds at
98%. Every import-hygiene suite passes unchanged, `tests/backtest/test_import_hygiene.py`
among them and unedited. Proven rather than claimed: a weekend-spanning H1
dataset that raises `BacktestError` without a session runs end to end with one,
processing every bar including the closure, while the same session leaves a
mid-week hole in an otherwise identical dataset failing closed.
