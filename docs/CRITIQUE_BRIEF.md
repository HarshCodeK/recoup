# CRITIQUE BRIEF — For Kimi K3 (read-only)

> Read this end to end before touching any file in `src/`, `tests/`,
> `fixtures/`, `eval.py`. The README in the repo root is canonical and
> supersedes anything here.

---

## 0. What you're being asked to do

Read every source file in this repo and produce a **structured critique** at
`docs/CRITIQUE_KIMI.md` covering:

1. Real bugs (correctness defects)
2. Design flaws (works in tests, fails in prod)
3. "Feels naah" smells (naming, structure, comment gaps)
4. Optimization wins (with diff-grade specificity)
5. Razorpay-context wins (what would make this more credible to a Razorpay
   engineer)

You are **read-only**. Do not edit any source file. The orchestrator (Hermes)
owns the implementation gate; you own the analysis.

---

## 1. What Razorpay is and what Track 4 is

**Razorpay** is India's largest payments aggregator. Merchants accept money
across 5+ surfaces:

- **UPI** (UPI Collect / UPI Intent / UPI Payments) — biggest by volume
- **Cards** (Visa/Mastercard/Amex via Razorpay's card processor)
- **Bank Transfer** (NEFT/RTGS/IMPS via RazorpayX)
- **Subscriptions** (UPI AutoPay + Card mandates, recurring)
- **Invoices** (Smart Collect 2.0 virtual accounts)

Each surface has its own record format, own ID space, own timestamps.
Reconciliation = matching a single real-world payment as it appears across
2+ of these surfaces.

**Razorpay AI Buildathon 2026** is a hackathon with 4 tracks. **Track 4 —
AI Finance Controller** is the only one where the AI is in front of money.
The judge's bar (verbatim from Harsh's brief):

> "Throughput plus measured accuracy plus an honest exception list.
> One cherry-picked match proves nothing."

That sentence is the north star. Every design choice in Recoup is a
consequence of it.

---

## 2. Why this exists / why it matters

A Razorpay merchant receiving payments on UPI + Card + Subscription has
~3 separate ledgers that should agree on every transaction but don't:
fees differ, timestamps drift, references truncate, some txns appear once,
some appear twice, some never reconcile. **Today this is manual Excel work.**

Recoup automates the deterministic parts and surfaces what it *can't*
automate as a typed exception list. The exception list is the product —
that's where the merchant looks every Monday.

---

## 3. Repository structure (canonical)

```
C:\Desktop\recoup\
├── README.md            # source of truth for what the project is
├── LICENSE              # MIT
├── requirements.txt     # pydantic, python-dateutil, pyyaml — 3 deps
├── eval.py              # end-to-end pipeline + headline reporter
├── demo_day1.py         # legacy Day-1 demo — kept for reference only
├── demo.sh
├── src/
│   ├── __init__.py
│   ├── data_model.py        (145 LOC)  Pydantic schemas, integer-paise amounts
│   ├── normalizer.py        (135 LOC)  5 source normalizers → 1 schema
│   ├── rule_engine.py       (179 LOC)  4 deterministic match rules
│   ├── match_engine.py      (244 LOC)  probabilistic feature-scored ranking
│   ├── exception_classifier.py (175 LOC) 6 reason codes (deterministic)
│   ├── diagnosis.py         (276 LOC)  LLM layer, sandboxed, refuses when unsure
│   ├── state_engine.py      ( 81 LOC)  7-state FSM
│   ├── policy_engine.py     (118 LOC)  economic floor, recovery cap, idempotency
│   ├── recovery.py          ( 83 LOC)  deterministic recovery playbook
│   ├── event_store.py       (186 LOC)  SQLite, SHA-256 hash chain, tamper detect
│   ├── orchestrator.py      (102 LOC)  pipeline wiring
│   ├── mcp_server.py        (259 LOC)  5 MCP tools over JSON-RPC 2.0 stdio
│   └── dashboard.py         (129 LOC)  Rich terminal UI
├── tests/                (121 tests, all passing in ~1.6s)
│   ├── test_data_model.py
│   ├── test_diagnosis.py
│   ├── test_eval.py
│   ├── test_event_store.py
│   ├── test_exception_classifier.py
│   ├── test_match_engine.py
│   ├── test_mcp_server.py
│   ├── test_normalizer.py
│   └── test_pipeline_day1.py
├── fixtures/
│   └── generate_dataset.py  deterministic seed=42, 5 sources
└── docs/
    ├── SESSION_CONTEXT.md   # ← read this first; the WHY behind the WHAT
    ├── CRITIQUE_BRIEF.md    # ← this file
    └── CRITIQUE_KIMI.md     # ← where you write your critique
```

Total: ~2,100 LOC production + ~2,300 LOC tests. Intentional flat
structure: one module per concern, no deep inheritance.

---

## 4. Headline numbers — measured, reproducible (seed=42)

```
Dataset:                 3000 transactions (5 sources)
Ground truth match pairs: 600

─── Auto-Match (rule + probabilistic engines) ───
Rule matched:            766
Probabilistic matched:   207
Total auto-matched:      973
Auto-match rate:         32.43%

─── Exceptions (deterministic classification) ───
Exceptions:              1221   (40.7% exception rate)
By reason:
  ambiguous                  819
  standalone_recurring       398
  amount_mismatch              4

─── Recovery (policy-gated) ───
Recovery proposed:       823
Allowed by policy:       292
Blocked by policy:       531
  amount_above_max_cap       524
  amount_below_economic_floor 7

─── Precision / Recall vs Ground Truth ───
Precision:               0.674
Recall:                  0.999
```

The 819 ambiguous count is **not a bug.** It's the product. Per principle
#5, "the exception list is the product." We do not auto-match ambiguous txns
just to bump precision.

---

## 5. Module-by-module mental model

### `data_model.py`
One `NormalizedTxn` Pydantic model. **All amounts stored as integer paise
(₹1 = 100 paise) to avoid float drift.** Six canonical sources enumerated.
This is the contract every other module honors.

### `normalizer.py`
5 parsers: UPI / Card / Bank / Subscription / Invoice. Each takes a
source-format dict and returns a `NormalizedTxn`. Edge cases worth
checking: empty strings, missing fields, extra fields, whitespace in IDs.

### `rule_engine.py`
4 deterministic match rules fire before any probabilistic or LLM logic.
Likely suspects: rule ordering, return types, what happens when no rule
fires (currently → escalate to probabilistic, right?).

### `match_engine.py`
Feature-scored ranking. Reads rule-no-match pairs and ranks candidate
matches by amount-distance + reference-similarity + time-delta + source.
Thresholds? Where? Look for off-by-one, threshold edge cases.

### `exception_classifier.py`
Deterministic classifier into 6 reasons. **This is what makes the exception
list reproducible.** If classification order is non-deterministic, the
exception list isn't. Worth scrutinizing.

### `diagnosis.py` (LLM layer)
The only place an LLM is called. Must:
- Take a typed `DiagnosisRequest`
- Return a typed `DiagnosisResult` (Pydantic)
- Refuse when uncertain (return UNKNOWN with refusal reason)
- Be sandboxed from the policy/state layers — cannot execute anything
This is the file Razorpay engineers will read first. **Read it like a
senior Razorpay engineer will.**

### `state_engine.py`
7-state FSM: INITIATED → PENDING → UNKNOWN → RECONCILING → SUCCESS / FAILED
/ HOLD. **Critical constraint:** no transition out of UNKNOWN except via
explicit reconciliation. No blind retry. The state machine is the last line
of defense before money moves.

### `policy_engine.py`
Hard invariants:
- Economic floor (recovery too cheap → don't auto-recover)
- Recovery cap (recovery too large → don't auto-recover)
- Idempotency key per (txn, reason, attempt)
- duplicate_fire → never-auto-recover (always)

### `recovery.py`
Maps reason → action_type. Idempotent. Outputs propose-not-execute. State
machine and policy gate decide what actually executes.

### `event_store.py`
SQLite (single file). Append-only. **SHA-256 hash chain over (prev_hash,
event_json)** — tampering any row breaks the chain. Has `verify_chain()`
method. This is the audit trail Razorpay compliance will want.

### `orchestrator.py`
Pipeline wiring. Should be a flat list of steps with no logic of its own.

### `mcp_server.py`
5 tools exposed over JSON-RPC 2.0 stdio: `reconcile_batch`,
`get_exceptions`, `replay_txn`, `propose_recovery`, `verify_audit_chain`.
This is the agent surface — Claude / Codex / Cursor / LangGraph can call
Recoup as an MCP server. Worth scrutinizing: input validation, error
format, tool description strings (MCP tool docs are what an AI agent reads
to decide what to call).

### `dashboard.py`
Rich terminal UI. Optional, demo-grade. Not critical to judge, but check
it actually runs without errors.

### `eval.py`
End-to-end pipeline runner. Generates dataset (or uses precomputed),
runs all engines, prints the headline numbers above. **This is what the
judge will run.**

### `fixtures/generate_dataset.py`
Deterministic dataset generator with seed=42. Documented schema.
Reproducibility is principle #4.

---

## 6. What Razorpay-specific context helps you catch

Things a generic Python reviewer won't catch but a Razorpay engineer will:

- **UPI vs Card fee asymmetry.** Razorpay charges different % per surface.
  Recoup's amount_mismatch exceptions should ideally tag the fee source.
- **Subscription "first month vs recurring" behavior.** Razorpay charges the
  first invoice differently from later ones. The standalone_recurring
  exception (398 of them) might be conflating these.
- **Invoice virtual accounts (Smart Collect 2.0).** These have a 24h TTL.
  Anything older should never auto-reconcile.
- **Webhook vs API-source-of-truth.** Real Razorpay gives both; sometimes
  the API returns settled but the webhook never fired. Worth checking
  whether `normalizer.py` preserves source-of-truth.
- **Settlement T+1 vs T+2.** Bank Transfer has different settlement
  windows. The time-delta feature in `match_engine.py` should respect
  this.

If you find something here that doesn't fit the current code, that's a
high-value finding.

---

## 7. What "good" output looks like

```markdown
## [BUG-01] Probabilistic threshold swallows edge case in match_engine.py

**File:** src/match_engine.py
**Lines:** 188-204
**Severity:** High (correctness — silently drops matches)

### What's wrong
The threshold `score >= 0.85` is hardcoded. When two UPI txns share an
amount AND a reference substring but differ by 1 paisa due to a fee
rounding diff, score is exactly 0.84 and the txn falls through to the
exception list as `ambiguous`.

### Why it matters
On real Razorpay data, fee-induced 1-paise deltas are common. We'd be
labeling real matches as ambiguous.

### Fix sketch
Add a fee-tolerance band: if abs(amount_delta) <= 2 (paise) AND
reference_similarity > 0.9, allow score >= 0.80 to count as match.

### Risk
Low. Adds a band, doesn't remove the threshold.

### Test gap
tests/test_match_engine.py has no test for 1-paise deltas.
```

Severity tags: **Critical** (could lose money or mis-classify settled txns)
/ **High** (real defect in normal Razorpay data) / **Medium** (edge case)
/ **Low** (polish) / **Info** (observation, no action).

---

## 8. Output contract

Write to `docs/CRITIQUE_KIMI.md`. Use this structure:

```markdown
# CRITIQUE — Kimi K3 on Recoup

## Summary
- Total findings: N
- By severity: Critical X, High Y, Medium Z, Low W, Info V
- Top-3 priorities: [list]

## Findings

### [BUG-01] <one-line title>
**File:** src/<file>.py
**Lines:** N-M
**Severity:** <tag>
... (template above)

### [DESIGN-02] <title>
...

### [SMELL-03] <title>
...

### [OPT-04] <title>
...

### [RAZORPAY-05] <title>
...

## What I deliberately did NOT flag
- <thing that looks bad but is intentional — with reason>
- ...

## What I'd build next if given 2 more hours
- <ordered list>
```

When you're done, save the file and exit. Do not commit, do not push,
do not modify anything else.

---

## 9. Constraints on you (Kimi)

- **Read-only.** No edits to anything outside `docs/CRITIQUE_KIMI.md`.
- **Be specific.** File + line numbers + diff sketch. No "consider improving
  error handling."
- **Be honest.** If the code is good, say so. "This is fine" is a valid
  finding.
- **Don't pad.** If you find 8 real things and 0 filler, that's 8. Don't
  invent issues to look thorough.
- **Respect the constraints in SESSION_CONTEXT.md.** No refactor-into-OOP,
  no framework swaps, no "remove the LLM," no "add a frontend."

---

## 10. Reference: GitHub mirror

The same repo is at https://github.com/HarshCodeK/recoup — you don't need
to pull from it; the local working tree at `C:\Desktop\recoup\` is the
source of truth and is more recent (last push was 2026-08-30). But if you
want to cross-check anything, the URL above works.

---

## 11. Time budget

The orchestrator will time-box your run to **90 minutes**. Spend:
- 10 min reading `README.md` + this brief + `SESSION_CONTEXT.md`
- 40 min reading every src file
- 20 min reading tests and eval.py
- 15 min writing `docs/CRITIQUE_KIMI.md`
- 5 min buffer

If you finish early, exit. If you hit 90 min mid-file, write what you
have and exit — partial critique is better than timeout.