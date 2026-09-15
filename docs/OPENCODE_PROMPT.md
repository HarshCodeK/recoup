# OpenCode Prompt — Recoup Critique (paste this into OpenCode)

> **Pre-flight:** OpenCode is already open in C:\Desktop\recoup. Use the **Plan** agent (read-only — no edits, only critique). This prompt is fully self-contained. Read the brief files it points to, then go through every source file and produce `docs/CRITIQUE_KIMI.md`.

---

You are a senior reviewer for a Razorpay AI Buildathon submission. The project
is `Recoup`, a deterministic reconciliation engine with an MCP-exposed control
plane. It targets Razorpay's **Track 4 — AI Finance Controller**. The submission
deadline is **3 September 2026**; today is 31 August 2026. You cannot break
tests. You cannot introduce floating-point drift in money. You cannot remove the
honest-exception list — it IS the product.

## Your job

Read **all three of these in order** before touching anything:

1. `docs/SESSION_CONTEXT.md` — how the project got built, the constraints the
   owner locked in, the multi-model review verdict that selected Track 4
2. `docs/CRITIQUE_BRIEF.md` — full project context: what Razorpay is, what
   Track 4 demands, Recoup's architecture, every module's purpose, design
   principles, current eval numbers, links to Razorpay docs and to the GitHub
   mirror
3. Every file under `src/`, `tests/`, `fixtures/`, plus `eval.py` and
   `README.md` — the source itself is the canonical truth, the briefs are
   summaries

Then write `docs/CRITIQUE_KIMI.md` with **four sections**:

### A. Verified-bugs / smells (must fix or justify)
- File path + line range + concrete observation
- Why it's wrong (what input breaks it, what invariant is violated, what
  Razorpay scenario exposes it)
- Proposed fix sketch (≤ 5 lines of code)
- Risk: Low / Medium / High — what happens if you don't fix it before submit

### B. Design choices that "feel off" but might be intentional
- Same shape (file, lines, observation, why-off, fix sketch)
- Mark each as `intentional` or `smell`. Owner will rule on each.

### C. Optimizations
- Anything wasted — redundant passes, O(n²) where O(n) suffices, repeated
  work across the pipeline
- Each item: file, lines, before-cost (concrete: ms, %), after-cost (estimate),
  complexity-of-fix (S / M / L)

### D. Razorpay-specific gaps
- Track 4 asks: throughput, accuracy, honest exception list, MCP exposure,
  agentic safety. Where does Recoup under-deliver on each of those bars?
- What's the cheapest improvement (≤ 30 min work) that closes the largest gap?

## Hard rules

- **Read-only.** Do not edit any file except writing the single critique doc.
  The owner wants a critique pass first, decides what to fix, runs the fixes
  themselves. If you propose code, it goes in the critique doc, not in `src/`.
- **No fabricated metrics.** If you don't see something measured, say
  "not measured by eval.py". Don't invent latency numbers.
- **Honest about what is good.** Section E in your critique doc: list 3–5
  things Recoup does well that the owner should not touch before submit.
- **Reference the file paths literally.** Don't paraphrase filenames.

## Acceptance for your critique doc

- Covers every `.py` file in `src/` (13 files, ~2,117 LOC) and `tests/`
  (8 files, 121 tests all passing)
- Each item has at minimum: file, line range, observation, recommendation
- No item proposes a fix that would break the test suite (mentally simulate
  `pytest tests/ -q` against your proposal)
- Bottom of doc: **10-item ranked fix list** ordered by risk-adjusted impact
  for the 3 Sep submission deadline. Owner will pick from this.

When you start, begin your reply with `R ```

## After you paste this

You will see OpenCode think for ~10-30 minutes. It will read the briefs, then
walk through each source file, then write `docs/CRITIQUE_KIMI.md` to the project
folder. When it's done, paste the contents of that file back to me in chat and
I'll review every recommendation, marking each as APPROVE / REJECT / DEFER.
Then we'll plan the fixes.

---

# Side artifacts already on disk

If OpenCode wants to see anything else:

- `C:\Desktop\recoup\docs\CRITIQUE_BRIEF.md` (same as `.txt`, 13 KB)
- `C:\Desktop\recoup\docs\SESSION_CONTEXT.md` (5 KB)
- `C:\Desktop\recoup\README.md` (canonical, 12 KB)
- GitHub mirror: https://github.com/HarshCodeK/recoup (same source tree)
- Test status: `cd C:/Desktop/recoup && PYTHONPATH=. python -m pytest tests/ -q` → 121 passed in 1.62s
- Eval status: `PYTHONPATH=. python eval.py 3000` prints headline numbers in the README

---

## Why Kimi K3 on Nvidia didn't work (read-only, you don't need to act on this)

When I tried to delegate this to OpenCode Kimi K3 via Nvidia myself, every chat
completion call to `nvidia/moonshotai/kimi-k3` timed out at the socket layer
after 60-180s (16 attempts, all the same). Probed the rest of the Nvidia
catalog from this network: all the Moonshot Kimi family endpoints return 404
on this account, all the Meta/Llama/Mistral/Nemotron chat endpoints are
marked EOL (HTTP 410). The only Nvidia chat model that responds today is
`nvidia/minimaxai/minimax-m3`. If OpenCode also has trouble reaching Kimi K3,
swap to that one — it returned "Pong" in 1.8s on a ping test.

---

# Final plan for after you get the critique back

1. Paste `docs/CRITIQUE_KIMI.md` contents here
2. I (Hermes, `kilo/minimax/minimax-m3:free`) review every item → APPROVE /
   REJECT / DEFER
3. I save the verdict to `docs/CRITIQUE_VERDICT.md`
4. You pick which APPROVED items to implement; I implement, gate tests, gate
   eval numbers
5. I update `docs/PROJECT_DETAILS.md` and push to GitHub
6. Telegram ping when done

The pipeline is built. Only the LLM call itself remains. Paste it into
OpenCode whenever you're ready.