"""Web dashboard for Recoup's audit trail and pipeline results.

Zero-dependency: uses Python's built-in http.server.
Run:  PYTHONPATH=. python -m src.web_dashboard
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from src.event_store import EventStore
from src.orchestrator import run_pipeline
from src.diagnosis import StubProvider
from fixtures.generate_dataset import generate

DB_PATH = str(Path(__file__).resolve().parent.parent / ".recoup" / "audit.db")


def _ensure_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def run_demo_pipeline():
    """Run the reconciliation pipeline on 3000 generated txns and persist events."""
    _ensure_db()
    store = EventStore(DB_PATH)
    txns, _ = generate(total_transactions=3000, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())

    with open(DB_PATH, "w") as f:
        pass  # reset

    store2 = EventStore(DB_PATH)
    for d in result.rule_decisions:
        if d.matched:
            store2.append("rule_match", {"a": d.txn_id_a, "b": d.txn_id_b}, d.txn_id_a)
    for exc in result.exceptions:
        store2.append("exception_classified", {"reason": exc.reason.value}, exc.txn_id)
    for diag in result.diagnoses:
        store2.append("llm_diagnosis", {"txn_id": diag.txn_id, "class": diag.diagnosis_class.value, "confidence": diag.confidence, "refused": diag.refused}, diag.txn_id)
    for _ in range(len(store2.get_all_events())):
        pass  # chain is built incrementally

    chain_ok, _ = store2.verify_chain()
    return {
        "total": result.report.total,
        "auto_matched": result.report.rule_matched + result.report.prob_matched,
        "exceptions": result.report.exceptions,
        "diagnosed": result.report.diagnosed,
        "refused": result.report.refused,
        "auto_match_rate": round(result.report.auto_match_rate, 1),
        "exception_rate": round(result.report.exception_rate, 1),
        "chain_ok": chain_ok,
        "event_count": len(store2.get_all_events()),
    }


def fetch_events(limit=50):
    store = EventStore(DB_PATH)
    events = store.get_all_events()
    return events[-limit:][::-1]


def fetch_txn_events(txn_id):
    store = EventStore(DB_PATH)
    return store.get_events_for_txn(txn_id)


HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Recoup — Reconciliation Control Plane</title>
<meta name="description" content="Deterministic multi-source payment reconciliation. Rule engine, probabilistic matching, LLM triage that can refuse, hash-chained audit.">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/house-ui.css">
</head><body>
<header class="topbar"><div class="wrap">
  <a class="brand" href="/"><span class="mark">◈</span> Recoup</a>
  <nav class="topnav"><a href="#pipeline" class="active">Pipeline</a><a href="#audit-panel">Audit</a><a href="#exceptions-panel">Exceptions</a><a href="https://github.com/HarshCodeK/recoup">GitHub</a></nav>
</div></header>
<div class="wrap">
  <div class="hero">
    <span class="eyebrow"><span class="dot"></span>153 tests green · SHA-256 hash-chained audit</span>
    <h1>LLM can triage. <span class="grad">LLM cannot move money.</span></h1>
    <p class="lead">Deterministic reconciliation across 5 sources. Rules first, probability second, LLM only for the ambiguous — and every step lands in a tamper-evident log.</p>
  </div>

  <div class="stats" id="stats">
    <div class="stat"><div class="v" id="stat-total">0</div><div class="l">Transactions</div></div>
    <div class="stat"><div class="v green" id="stat-matched">0</div><div class="l">Auto-matched</div></div>
    <div class="stat"><div class="v amber" id="stat-exc">0</div><div class="l">Exceptions</div></div>
    <div class="stat"><div class="v blue" id="stat-rate">0%</div><div class="l">Auto-match rate</div></div>
    <div class="stat"><div class="v" id="stat-events">0</div><div class="l">Audit events</div></div>
    <div class="stat"><div class="v green" id="stat-chain">✓</div><div class="l">Chain integrity</div></div>
  </div>

  <section id="pipeline">
    <h2>Pipeline</h2>
    <p class="sub">Five stages. The first two must be right; the LLM is allowed to be wrong safely.</p>
    <div class="stepper">
      <div class="pstep done"><div class="node">◉</div><div class="lbl">Normalize</div><div class="desc">5 sources → 1 schema</div></div>
      <div class="pstep done"><div class="node">⧉</div><div class="lbl">Rules</div><div class="desc">exact-match engine</div></div>
      <div class="pstep done"><div class="node">◈</div><div class="lbl">Probability</div><div class="desc">Jaccard scoring</div></div>
      <div class="pstep done"><div class="node">✋</div><div class="lbl">Triage</div><div class="desc">LLM · can refuse</div></div>
      <div class="pstep done"><div class="node">🧾</div><div class="lbl">Audit</div><div class="desc">hash-chained</div></div>
    </div>
    <div class="card"><h3>▶ Demo <span class="spacer"></span><button class="btn sm" id="demo-btn" onclick="runDemo()">Re-run 3,000-txn pipeline</button></h3>
      <p class="muted">Regenerates the deterministic dataset (seed 42), reruns rule → probability → classify → diagnose, rebuilds the chain.</p>
    </div>
  </section>

  <section id="audit-panel">
    <h2>Audit trail</h2>
    <p class="sub">Last 50 events, live. Click any row to inspect its payload.</p>
    <div class="card"><h3>◈ Events <span class="spacer"></span><span class="pill ok" id="chain-pill">chain: checking…</span></h3>
      <div id="audit"></div>
      <div class="banner" id="txn-detail" style="display:none;margin-top:12px"></div>
    </div>
  </section>

  <section id="exceptions-panel">
    <h2>Exception breakdown</h2>
    <p class="sub">What the engines could not auto-match, by reason.</p>
    <div class="card"><h3>⚠ Queue</h3><div id="exceptions"></div></div>
  </section>
</div>
<footer><div class="wrap"><a href="https://github.com/HarshCodeK/recoup">github.com/HarshCodeK/recoup</a><span style="float:right" class="muted">MIT · deterministic money math</span></div></footer>

<script>
async function fetchJSON(url){const r=await fetch(url);if(!r.ok)throw new Error(r.status);return r.json();}

async function runDemo(){
  const btn=document.getElementById('demo-btn');btn.disabled=true;btn.innerHTML='<span class="spin"></span> Running pipeline…';
  try{const r=await fetchJSON('/api/run-demo');updateStats(r);await renderAudit();}catch(e){btn.textContent="Failed — retry";}
  btn.disabled=false;btn.textContent="Re-run 3,000-txn pipeline";
}

function updateStats(s){
  document.getElementById("stat-total").textContent=s.total;
  document.getElementById("stat-matched").textContent=s.auto_matched;
  document.getElementById("stat-exc").textContent=s.exceptions;
  document.getElementById("stat-rate").textContent=s.auto_match_rate+"%";
  document.getElementById("stat-events").textContent=s.event_count;
  const ch=document.getElementById("stat-chain");
  ch.textContent=s.chain_ok?"✓":"✗"; ch.className='v '+(s.chain_ok?'green':'red');
  const pill=document.getElementById("chain-pill");
  pill.textContent=s.chain_ok?"chain: intact":"chain: BROKEN"; pill.className='pill '+(s.chain_ok?'ok':'bad');
  const exc=document.getElementById("exceptions");
  if(exc && s.exceptions!=null){
    exc.innerHTML=`<div style="display:flex;gap:14px;align-items:center"><div style="font-family:var(--mono);font-size:26px">${s.exceptions}</div>
    <div class="muted">open exceptions · ${s.refused||0} LLM refusals (safe) · match rate ${s.auto_match_rate}%</div></div>`;
  }
}

function showTxn(seq, type, txn, time, payload){
  const d=document.getElementById("txn-detail");
  d.style.display='block';
  d.innerHTML=`<b>#${seq} ${type}</b> · ${txn||'global'} · ${time}<br/><span class="muted">${payload}</span>`;
  d.scrollIntoView({behavior:'smooth',block:'nearest'});
}

async function renderAudit(){
  try{
    const events=await fetchJSON('/api/events?limit=50');
    document.getElementById("audit").innerHTML=events.length===0
      ?'<p class="empty">No events yet. Run the demo to populate the audit trail.</p>'
      :'<table class="hu"><thead><tr><th>#</th><th>Type</th><th>Txn</th><th>Time</th><th>Payload</th></tr></thead><tbody>'+
      events.map(e=>{const bad=/reject|violation|refus|fail/.test(e.event_type);const full=JSON.stringify(e.payload).replace(/</g,'&lt;');
        return`<tr class="clickable" onclick='showTxn(${e.seq},"${e.event_type}","${e.txn_id||''}","${new Date(e.created_at).toLocaleTimeString()}","${full.slice(0,400)}")'>
        <td class="muted">#${e.seq}</td>
        <td><span class="pill ${bad?'bad':'ok'}">${e.event_type}</span></td>
        <td class="mono">${e.txn_id||"<span class='muted'>global</span>"}</td>
        <td class="muted">${new Date(e.created_at).toLocaleTimeString()}</td>
        <td class="muted" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${full.slice(0,110)}</td>
      </tr>`;}).join("")+"</tbody></table>";
  }catch(e){
    document.getElementById("audit").innerHTML='<p class="empty">Dashboard server not running.</p>';
  }
}

async function init(){try{const s=await fetchJSON('/api/stats');updateStats(s);}catch(e){}await renderAudit();setInterval(async()=>{try{const s=await fetchJSON('/api/stats');updateStats(s);}catch(e){}await renderAudit();},4000);}
init();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/dashboard":
            self._send_html(HTML)
        elif path == "/assets/house-ui.css":
            css_path = Path(__file__).resolve().parent.parent / "web" / "assets" / "house-ui.css"
            if css_path.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "text/css;charset=utf-8")
                self.end_headers()
                self.wfile.write(css_path.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()
        elif path == "/api/stats":
            self._send_json(_safe_stats())
        elif path == "/api/events":
            limit = int(qs.get("limit", ["50"])[0])
            self._send_json(fetch_events(limit))
        elif path == "/api/txn":
            txn_id = qs.get("id", [""])[0]
            self._send_json(fetch_txn_events(txn_id) if txn_id else [])
        elif path == "/api/run-demo":
            self._send_json(run_demo_pipeline())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass

    def _send_html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html;charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())


def _safe_stats():
    _ensure_db()
    try:
        events = fetch_events(50)
        total = 0
        matched = 0
        exc = 0
        refused = 0
        for e in events:
            t = e["event_type"]
            if t == "rule_match":
                matched += 1
            elif t == "exception_classified":
                exc += 1
            elif t == "llm_diagnosis":
                if e.get("payload", {}).get("refused"):
                    refused += 1
        total = len(events)
        chain_ok, _ = EventStore(DB_PATH).verify_chain()
        return {
            "total": total,
            "auto_matched": matched,
            "exceptions": exc,
            "refused": refused,
            "auto_match_rate": round(matched / max(total, 1) * 100, 1),
            "event_count": total,
            "chain_ok": chain_ok,
        }
    except Exception:
        return {"total": 0, "auto_matched": 0, "exceptions": 0, "auto_match_rate": 0, "event_count": 0, "chain_ok": False}


def main():
    _ensure_db()
    port = int(__import__("os").environ.get("PORT", "8300"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"\n✅ Recoup dashboard on http://localhost:{port}")
    print(f"   Run /api/run-demo to generate 3,000 transactions")
    print()
    server.serve_forever()


if __name__ == "__main__":
    main()
