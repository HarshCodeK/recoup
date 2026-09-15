# Session Context — Why Recoup Looks The Way It Does

> Concise background for Kimi K3. Skip this and you'll suggest fixes that
> contradict constraints Harsh already locked in.

## 1. The setup

- **Applicant:** Harsh Kharavle (HarshCodeK on GitHub, harsh.kharavle@gmail.com)
- **Target:** Razorpay AI Builder Intern — ₹75K/month — **deadline Sept 3, 2026**
- **Track chosen:** Track 4 — AI Finance Controller
- **Why this track:** Harsh's honest market read is that winners ship *agentic + RAG
  + MCP + eval* with quantified outcomes (latency / cost / precision). Track 4 is
  the only one where finance correctness is non-negotiable, so it's also the only
  one that lets a candidate show *engineering judgement* rather than prompt
  cleverness.

## 2. The 8-model pre-review

Before any code was written, Harsh asked **8 different AI models** (ChatGPT,
Claude, DeepSeek, Grok, Lingua, LongCat, Perplexity, Qwen) to pitch an MVP for
this track. He then asked **me** (Hermes / M3-free) to score them and pick.

The winner was the **Track-4 multi-source reconciliation** concept (Lingua's
framing + Qwen's engineering discipline). Other pitches were rejected because
they either: (a) needed production infra Harsh doesn't have, (b) couldn't ship
working code in the window, or (c) were "yet another RAG wrapper."

**Recoup is built from scratch.** Zero lines of code were copied from those
8 planning docs. Every line in `C:\Desktop\recoup\` was written by Hermes on
Aug 29, 2026.

## 3. The constraints that shaped every design choice

These are non-negotiable. Treat them as laws:

1. **The LLM never executes anything.** It only classifies ambiguous exceptions
   into a typed enum. The Policy Engine decides. The State Machine enforces.
2. **UNKNOWN is a first-class state.** No transition out of UNKNOWN except
   through explicit reconciliation. No blind retry, ever.
3. **Synthetic data is honest.** Dataset is generated deterministically
   (seed=42) with documented schema. Real Razorpay test-mode would slot in via
   the same ingestion code — we admit this is not wired today.
4. **Every metric is reproducible.** Same seed → same numbers. The headline
   numbers in `README.md` are what `eval.py 3000` actually prints.
5. **The exception list is the product.** Track-4 bar:
   *"Throughput plus measured accuracy plus an honest exception list.
   One cherry-picked match proves nothing."* Recoup is built around that sentence.

## 4. Current headline numbers (eval.py 3000, seed=42)

```
Auto-match rate:       32.43%   (rule 766 + prob 207 = 973 / 3000)
Exception rate:        40.7%    (1221 / 3000)
  - ambiguous:               819
  - standalone_recurring:    398
  - amount_mismatch:           4
Precision vs GT:       0.674
Recall vs GT:          0.999
Tests:                 121/121 passing in ~1.6s
```

The precision/recall asymmetry is *intentional* — recall is near-perfect
because we don't pretend. Precision is moderate because 819 ambiguous txns
get surfaced rather than silently force-matched. **Do not propose tightening
precision by silently force-matching** — that breaks principle #5.

## 5. What the user (Harsh) values in a critique

- **Concrete, code-grounded.** "Add an index" is bad. "Add an index on
  `events.txn_id` in `event_store.py:121` — currently a full scan because
  `txn_id` isn't indexed" is good.
- **Razorpay-specific.** Generic Python improvement tips are noise. The useful
  question is: does this code mirror how Razorpay's real APIs/flows actually
  work?
- **Risk-aware.** Distinguish *correctness risk* (touching money/state) from
  *polish risk* (cosmetic). Don't recommend rewrites of `state_engine.py`
  without explaining the test cost.
- **Honest about what NOT to change.** Sometimes the answer is "this is
  fine, here's why." Say that when it's true.

## 6. What we DON'T want from the critique

- Refactor-everything-into-OOP suggestions (Recoup is intentionally flat —
  one module per concern, ~100-200 LOC each).
- "You should use SQLAlchemy / LangChain / X framework." Recoup uses
  stdlib sqlite3 + pydantic + rich on purpose (deterministic, no magic).
- Suggestions to remove the LLM layer entirely — that's the Razorpay track's
  whole point. Don't strip the agent story.
- "Add a frontend." Out of scope; Rich terminal dashboard is the deliverable.

## 7. What I'm asking Kimi to do

1. **Read every file in `src/`, `tests/`, `fixtures/`, `eval.py`** end to end.
2. **Find real defects.** Bugs, race conditions, edge cases the tests miss,
   amounts in paise but stored as float somewhere, unsafe SQL, swallowed
   exceptions, etc.
3. **Find design flaws.** Things that look fine in tests but will fail in
   production (e.g. amount precision in probabilistic scoring, weak classifier
   on edge amounts, state machine transitions the tests don't cover).
4. **Find "this feels naah" smells.** Code that works but reads as if a tired
   intern wrote it at 4am. Naming, structure, comment gaps.
5. **Find optimization wins.** SQLite indexes, redundant computation,
   avoidable allocations, large function splits.
6. **Find missed Razorpay-context wins.** Things that would make this more
   credible to a Razorpay engineer specifically.
7. **Output:** structured critique at `docs/CRITIQUE_KIMI.md`.

You (Kimi) are running **read-only**. You may not edit source files. You
report; the orchestrator decides what gets implemented.