# Recoup — Deterministic Multi-Source Reconciliation Control Plane

> A payment-reconciliation control plane for fintech ops teams that need to
> **match transactions across sources, classify what cannot be matched, and
> safely decide what to recover** — without ever letting an LLM touch money.

[![tests](https://img.shields.io/badge/tests-153%20passing-brightgreen)](#)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](#)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](#)
[![mcp](https://img.shields.io/badge/MCP-compatible-purple)](#)

---

## What this actually is

**Recoup** is a working control plane built for the Razorpay AI Builder
challenge (Track 4 — multi-source reconciliation). It is **not** a
wrapper around an LLM, **not** a fintech demo with hand-wavy numbers, and
**not** an agent that performs money actions.

The flow is end-to-end and real:

```
                  5 sources (UPI / netbanking / card / wallet / ledger)
                                       │
                                       ▼
                ┌──────────────────────────────────────┐
                │  normalizer   →   rule engine        │  ← deterministic
                │  match engine →   exception class.   │     safe-to-be-wrong layers
                └──────────────────────────────────────┘
                                       │ ambiguous (819 / 3000 in eval)
                                       ▼
                ┌──────────────────────────────────────┐
                │  LLM triage  (classify-only, no     │  ← can be wrong safely
                │  tool use, no money authority)       │
                └──────────────────────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────┐
                │  policy engine (floor / cap / black- │  ← must be right
                │  list)   →   recovery (gated)        │
                └──────────────────────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────┐
                │  state machine   →   audit chain     │  ← append-only,
                │  (UNKNOWN first-class state)        │     hash-chained SQLite
                └──────────────────────────────────────┘
```

Every layer "can be wrong safely" except the two that must be right
(policy, state transitions). Everything is logged in a tamper-evident
SHA-256 hash-chained audit store.

---

## Real numbers (measured, reproducible)

From `python eval.py` (default = 3000 transactions, seed=42, deterministic):

| Metric | Value |
|---|---|
| Total transactions | **3000** |
| Ground-truth match pairs | 600 |
| Auto-match rate (rule + prob.) | **32.43%** (973 / 3000) |
| Exceptions (could not auto-match) | 1221 (40.7%) |
| Ambiguous (escalated to LLM triage) | **819** |
| Recovery proposed | 823 |
| Recovery **allowed** by policy | **292** (35.5% of proposed) |
| Recovery **blocked** by policy | **531** (64.5% of proposed) |
| **Precision vs ground truth** | **0.674** |
| **Recall vs ground truth** | **0.999** |
| **F1** | **0.805** |
| Audit events recorded | **3013** |
| Audit chain integrity | **OK** |
| Test suite | **153 / 153 passing** in ~10 s |

Reproducible — re-run produces identical numbers (seed=42).

---

## What Recoup does

- **Normalizes** transactions from 5 source shapes (UPI, netbanking, card, wallet, ledger) into a single canonical model. Integer paise everywhere; no float drift.
- **Auto-matches** using a deterministic rule engine + a bounded probabilistic engine (Jaccard on reference tokens).
- **Classifies** what cannot be auto-matched into typed reasons (`amount_mismatch`, `missing_reference`, `timing_window`, `duplicate_fire`, `missing_txn`, `ambiguous`, `standalone_recurring`).
- **Triages ambiguous cases with an LLM** — typed, schema-enforced, never tools, never money authority. Refuses honestly if uncertain.
- **Gates recovery with a policy engine** — economic floor (₹5.00), max cap (₹10,00,000.00), reason blacklist, manual-approval threshold.
- **Persists state in an FSM** where `UNKNOWN` is first-class (never silently retried).
- **Records everything** in a SHA-256 hash-chained SQLite audit log; tampering is detected by `verify_chain()`.
- **Exposes 5 tools over MCP** (JSON-RPC 2.0 over stdio) for an external agent to drive the pipeline.

---

## Quickstart

```bash
# 1. Install
pip install -r requirements.txt

# 2. (Optional) wire a real LLM for ambiguous triage
cp .env.example .env
# edit .env with your OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
# defaults work against kilo.ai's free tier

# 3. Run the offline test suite (153 tests, no network)
PYTHONPATH=. python -m pytest

# 4. Run the eval pipeline
PYTHONPATH=. python eval.py

# 5. Run the day-1 rule-engine demo (no LLM, no DB)
PYTHONPATH=. python demo_day1.py

# 6. (Optional) verify the LLM is wired against your gateway
PYTHONPATH=. python scripts/live_llm_smoke.py

# 7. (Optional) run the MCP server
PYTHONPATH=. python -m src.mcp_server
```

---

## Repository layout

```
recoup/
├── README.md                   # this file
├── pyproject.toml              # project metadata + tool config
├── requirements.txt            # pydantic, rich, httpx, etc.
├── .env.example                # template for LLM env config
├── eval.py                     # end-to-end pipeline eval (3000 txns)
├── demo_day1.py                # rule-engine demo, no LLM, no DB
├── scripts/
│   └── live_llm_smoke.py       # verify LLM provider wiring end-to-end
├── src/
│   ├── data_model.py           # Pydantic models — money in paise (int)
│   ├── normalizer.py           # 5 source shapes → canonical txn
│   ├── rule_engine.py          # deterministic match rules
│   ├── match_engine.py         # probabilistic Jaccard match
│   ├── exception_classifier.py # typed reason classification
│   ├── diagnosis.py            # LLM triage (typed, refuse-capable)
│   │                           #   + OpenAICompatibleProvider (kilo, etc.)
│   ├── policy_engine.py        # recovery gates (floor / cap / blacklist)
│   ├── recovery.py             # policy-gated recovery actions
│   ├── state_engine.py         # FSM with UNKNOWN as first-class
│   ├── event_store.py          # SHA-256 hash-chained SQLite audit log
│   ├── mcp_server.py           # JSON-RPC 2.0 MCP server (5 tools)
│   ├── orchestrator.py         # pipeline glue
│   └── dashboard.py            # Rich-text CLI dashboard
├── fixtures/
│   └── generate_dataset.py     # deterministic synthetic txn generator
├── tests/                      # 153 offline tests (pytest)
└── docs/
    ├── limitations.md          # what Recoup does NOT do (required reading)
    ├── CRITIQUE_BRIEF.md       # external critique notes
    ├── OPENCODE_PROMPT.md      # session context
    └── SESSION_CONTEXT.md      # session context
```

---

## The 5 MCP tools

Run with `python -m src.mcp_server`. JSON-RPC 2.0 over stdio.

| Tool | Purpose |
|---|---|
| `reconcile_batch` | Run the full pipeline on a synthetic dataset; return auto-match rate + exception breakdown + audit chain status. |
| `get_exceptions` | Return the unresolved transactions with typed reasons. |
| `replay_txn` | Return the real audit-event timeline for a transaction. Reads from the persistent event store, **not** hardcoded. |
| `propose_recovery` | Test whether a recovery action would be allowed by policy. **Does not** execute. |
| `verify_audit_chain` | Walk the hash chain end-to-end; return `chain_ok` + `broken_at_seq` (or null). |

A one-liner smoke test:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"reconcile_batch","arguments":{"dataset_size":50}}}' \
  | python -m src.mcp_server
```

---

## Trust boundaries — the load-bearing claims

These are the design choices that separate "I used an LLM" from "I thought
about money." If you only have 30 seconds, read this section.

1. **The LLM cannot move money.** `diagnosis.py` rejects tool use, function calling, and network calls. Its output is parsed through a typed Pydantic schema and a confidence floor (0.5). Below that floor, the diagnosis is `REFUSED` and the txn is escalated to human review.
2. **Money is integer paise everywhere.** No `float` in any money code path. Pydantic models reject `float` for `amount_paise`.
3. **`UNKNOWN` is a first-class state.** The state machine has terminal `UNKNOWN` and `EXCEPTION` states. The recovery layer never retries blindly.
4. **The audit chain is append-only and tamper-evident.** Every event's SHA-256 hash includes the previous event's hash. `verify_chain()` walks the entire chain on demand. Tests confirm `tamper_for_testing()` causes `verify_chain()` to return `(False, broken_at_seq)`.
5. **Policy gates are deterministic and explicit.** Floor, cap, reason blacklist, manual-approval threshold — all in `policy_engine.py`, all unit-tested.
6. **The eval is reproducible.** Seed=42 produces identical numbers on every run.

---

## Honest limitations

Read [`docs/limitations.md`](docs/limitations.md) for the full list. Summary:

- Synthetic data only. No real Razorpay adapter (Razorpay test-mode adapter not built — see `docs/limitations.md`).
- LLM route is wired but the eval run uses `StubProvider()` for repeatability. Use `scripts/live_llm_smoke.py` to exercise the live provider.
- Probabilistic match uses bounded Jaccard on tokens — no learned embedding model. Good enough for synthetic; would need retraining on real data.
- Dashboard is Rich-text CLI; no web UI.
- Single-process, single-host. No horizontal scaling.

---

## Tests

```bash
PYTHONPATH=. python -m pytest
# 153 passed in ~10s
```

Tests are offline by default — `tests/conftest.py` forces the LLM provider to `StubProvider()` so the suite is deterministic and fast. The live provider is exercised in `scripts/live_llm_smoke.py`.

New tests added in this revision:

- `tests/test_llm_provider.py` — 10 tests covering the OpenAI-compatible provider (mocked) + provider selection logic.
- `tests/test_mcp_server.py` — rewritten to assert the singleton event-store persistence + replay-from-real-store behavior.

---

## License

MIT.