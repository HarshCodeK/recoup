# Recoup — Honest Limitations

This document is the canonical list of what Recoup **does not** do. It is
required reading before drawing conclusions from the README or the eval report.

## Not built

| Capability | Status | Why |
|---|---|---|
| **Live Razorpay / Stripe / bank adapter** | Not implemented | The reconciliation pipeline consumes a `NormalizedTxn` schema. A real adapter would emit that schema from the source's webhook payload. The adapter boundary exists; no production connector is wired. |
| **Persistent customer database** | Not implemented | Recoup is stateless across batches. All state lives in the audit log (SQLite, SHA-256 chained). No customers, no merchant accounts, no API keys stored. |
| **Recovery execution** | **Never automatic** | `propose_recovery()` returns a proposal + an idempotency key. A human or downstream automation must approve and call the actual refund/initiate endpoint. The system has no outbound money path. |
| **Multi-tenant / auth** | Not implemented | The MCP server has no token, no auth, no rate limit. It is a local stdio server. Running it on a public host requires an auth layer. |
| **Production vector DB for retrieval** | Skipped | Diagnosis works on bounded sanitized context (≤ 20 sibling transactions). No embeddings, no semantic search. The exception → diagnosis path is a single LLM call. |
| **Web UI / dashboard beyond CLI** | `src/dashboard.py` is a terminal Rich dashboard | No React / Next / Vite frontend. |

## Known design choices (not bugs, but worth saying)

1. **LLM refusal is a feature, not a failure.** When the LLM is uncertain,
   it returns `REFUSED`. The system then treats the exception as "needs review"
   rather than inventing a classification. Of the 819 ambiguous transactions
   in the headline run, all 819 refused — and that is the intended behavior
   for the StubProvider. The OpenAICompatibleProvider (Nemotron on kilo)
   produces real classifications when wired.

2. **Event store regenerates audit per-call in `eval.py`.** The headline
   eval report opens an in-memory SQLite store and writes events for the
   run, then closes it. The MCP server now uses a singleton persistent
   store (default `./.recoup/audit.db`). The two paths are intentionally
   different — eval stays reproducible-by-seed, MCP stays queryable.

3. **Synthetic dataset only.** `fixtures/generate_dataset.py` produces
   deterministic test data with seeded noise (typos in references, UPI
   amount drift, duplicate credit + debit pairs). It does not look like
   real Razorpay test-mode traffic. A real adapter would carry webhook
   signatures, idempotency keys, and gateway-specific error codes.

4. **Rule engine covers 5 reference / amount patterns.** It does not
   use string similarity beyond exact+normalized matches. Fuzzy matching
   is in the probabilistic layer (`match_engine.py`), not the rule layer.

5. **Integer paise everywhere.** All money math is in integer paise
   (₹1.00 = 100 paise). No floats touch a money path. `amount_inr` in
   the MCP responses is `amount_paise / 100` for display only.

## What needs to happen before this could be called production

1. Build a real Razorpay test-mode adapter (the `NormalizedTxn` boundary is ready).
2. Add auth + rate limiting to the MCP server.
3. Move audit store from local SQLite to a managed durable store
   (Postgres with append-only WAL, or a hosted ledger).
4. Run a real-world reconciliation against 90 days of test-mode traffic
   and compare against Razorpay's own reconciliation output.
5. Add an evaluation harness with held-out test cases, not just the
   synthetic generator.

None of the above is implemented today. Anything in the README that
implies otherwise is wrong; the README was the first thing to be
corrected in this revision.