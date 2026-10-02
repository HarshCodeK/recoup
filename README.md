# Recoup — Deterministic Multi-Source Reconciliation Control Plane

> A reconciliation control plane for fintech operations: normalize transactions,
> match across sources, classify exceptions, and propose policy-gated recovery
> actions — without giving an LLM authority to move money.

[![tests](https://img.shields.io/badge/tests-162%20passing-brightgreen)](#tests)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](#quickstart)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](#license)
[![mcp](https://img.shields.io/badge/MCP-compatible-purple)](#the-5-mcp-tools)

## What this actually is

Recoup is a working control-plane implementation for multi-source payment
reconciliation. The core pipeline is deterministic first and probabilistic
second:

```
5 source shapes
    |
    v
normalize -> deterministic rules -> bounded probabilistic matching
    |                         |
    +-------------------------+---- unmatched
                                      |
                                      v
                              typed exception reasons
                                      |
                                      v
                              LLM triage (ambiguous only)
                                      |
                                      v
                              recovery proposal
                                      |
                                      v
                         deterministic policy gate
                                      |
                                      v
                         state / audit infrastructure
```

The LLM only classifies ambiguous exceptions. Its output is schema-validated
and low-confidence output becomes `REFUSED`. Recovery proposals are created
by deterministic mappings and then checked by the policy engine; there is no
outbound money-execution path in this repository.

## What is measured

The included evaluation is deterministic and uses a synthetic 3,000-transaction
dataset with `seed=42`.

| Metric | Current recorded value |
|---|---:|
| Total transactions | **3000** |
| Ground-truth match pairs | **600** |
| Auto-match rate (rule + probabilistic) | **32.43%** (973 / 3000) |
| Exceptions | **1221** (40.7%) |
| Ambiguous exceptions | **819** |
| Recovery proposals | **823** |
| Policy-allowed proposals | **292** |
| Policy-blocked proposals | **531** |
| Audit events written by the evaluator | **3013** |
| Offline tests | **162** |

The current evaluator computes **pair-level** precision, recall, and F1. Older
transaction-ID coverage figures such as `0.674 / 0.999 / 0.805` are retired and
must not be quoted as current pair-level metrics. Run `PYTHONPATH=. python eval.py`
to regenerate the current figures.

## What Recoup does

- **Normalizes** UPI, netbanking, card, wallet, and ledger-shaped records into a
  canonical transaction model. Money is represented as integer paise.
- **Auto-matches** with a deterministic rule engine followed by a bounded
  probabilistic matcher using reference-token similarity.
- **Classifies exceptions** into typed reasons such as amount mismatch, missing
  reference, timing window, duplicate fire, missing transaction, ambiguity, and
  recurring standalone records.
- **Triages only ambiguous exceptions with an LLM.** The model receives bounded
  structured evidence, cannot call tools, and cannot issue recovery commands.
- **Proposes recovery deterministically.** `recovery.py` maps an exception
  reason to an action type; `policy_engine.py` independently decides whether
  the proposal is allowed.
- **Applies explicit recovery limits.** The current defaults are a ₹100 minimum,
  a ₹10,000 maximum, a maximum of 3 recovery attempts per event, and a
  one-hour cooldown configuration.
- **Provides an FSM and audit infrastructure.** `state_engine.py` constrains
  legal transaction transitions; `event_store.py` provides an append-only,
  SHA-256 hash-chained SQLite log with chain verification.
- **Exposes five MCP tools** over JSON-RPC 2.0/stdio so an external agent can
  drive reconciliation, inspect exceptions, replay a transaction timeline,
  test a recovery proposal, and verify the audit chain.
- **Includes a zero-dependency web dashboard** in `src/web_dashboard.py` for
  local inspection and re-running the synthetic demo.

## Quickstart

```bash
pip install -r requirements.txt

# Run the offline suite
PYTHONPATH=. python -m pytest

# Run the deterministic evaluation
PYTHONPATH=. python eval.py

# Run the day-1 rule-engine demo
PYTHONPATH=. python demo_day1.py

# Optional: exercise the configured live LLM provider
PYTHONPATH=. python scripts/live_llm_smoke.py

# Optional: start the MCP server
PYTHONPATH=. python -m src.mcp_server

# Optional: start the local web dashboard
PYTHONPATH=. python -m src.web_dashboard
# open http://localhost:8300
```

The default evaluation uses `StubProvider()` for repeatability. It does not
require a network or a real LLM.

## Repository layout

```
recoup/
├── src/
│   ├── data_model.py
│   ├── normalizer.py
│   ├── rule_engine.py
│   ├── match_engine.py
│   ├── exception_classifier.py
│   ├── diagnosis.py
│   ├── policy_engine.py
│   ├── recovery.py
│   ├── state_engine.py
│   ├── event_store.py
│   ├── mcp_server.py
│   ├── web_dashboard.py
│   └── orchestrator.py
├── fixtures/
│   └── generate_dataset.py
├── scripts/
│   └── live_llm_smoke.py
├── tests/
├── eval.py
└── docs/
    ├── INTERVIEW_QA.md
    └── limitations.md
```

## The 5 MCP tools

Run `PYTHONPATH=. python -m src.mcp_server`.

| Tool | Purpose |
|---|---|
| `reconcile_batch` | Run reconciliation on a deterministic synthetic dataset and return matching/exception/audit results. |
| `get_exceptions` | Return unresolved transactions with typed exception reasons. |
| `replay_txn` | Return the persistent audit-event timeline for a transaction. |
| `propose_recovery` | Test whether a recovery proposal would pass deterministic policy. **It does not execute the action.** |
| `verify_audit_chain` | Verify the SHA-256 event chain and report where it breaks, if anywhere. |

## Trust boundaries — the load-bearing claims

1. **The LLM cannot move money.** `diagnosis.py` is a classify-only interface.
   It rejects tool/function use by construction of the provider call, validates
   the result, and turns malformed or low-confidence output into `REFUSED`.
2. **Money is integer paise.** Financial amounts use integer paise in the data
   model and policy calculations; INR floats are presentation-only.
3. **Recovery is policy-gated.** The LLM does not choose the recovery action and
   cannot override the deterministic policy engine.
4. **State transitions are explicit.** `state_engine.py` defines the legal FSM
   transitions, including `UNKNOWN`, rather than silently retrying unknown
   transactions.
5. **The audit store is tamper-evident, not magically immutable.** Each event
   hash includes the previous event hash, and `verify_chain()` detects
   subsequent tampering. The surrounding application still has to use the store
   consistently for an execution path to be fully audited.
6. **The evaluation is synthetic.** Its numbers show behavior on the included
   generator, not production performance on live payment traffic.

## Why the LLM is not in the recovery path

This is the central design decision. The LLM answers a diagnostic question:
“why could this transaction not be matched?” It does not answer “what money
should I move?” Recovery proposals come from deterministic playbooks, and the
policy engine applies the hard economic and safety constraints independently.

That keeps the probabilistic component in a place where a wrong answer can be
reviewed, instead of letting model output directly authorize a financial action.

## Evaluation notes

The headline non-pair metrics are reproducible from `eval.py`. The matching
precision/recall/F1 calculation is pair-based: each predicted unordered
transaction pair is compared with each ground-truth unordered pair.

The evaluator also writes a bounded audit trace to an in-memory SQLite store.
That trace covers match decisions, exception classifications, and LLM diagnoses;
it should not be read as proof that every policy/state/recovery operation in a
production run is automatically logged.

## Honest limitations

Read [`docs/limitations.md`](docs/limitations.md) before presenting this as a
production system.

The main gaps are: no live Razorpay/Stripe/bank adapter, no recovery execution
path, no auth or rate limiting for the local MCP server, synthetic data only,
single-process deployment assumptions, and an LLM diagnosis layer that is
deliberately bounded rather than a general agent.

## Interview preparation

[`docs/INTERVIEW_QA.md`](docs/INTERVIEW_QA.md) is written against the current
code and focuses on the trust boundaries, evaluation methodology, and the
tradeoffs an interviewer is most likely to probe.

## License

MIT.
