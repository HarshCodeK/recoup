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
<title>Recoup — Web Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0b0f14;--card:#121822;--line:rgba(31,42,55,.6);--text:#e5e7eb;--muted:#9ca3af;--accent:#3b82f6;--ok:#22c55e;--bad:#ef4444;--warn:#f59e0b;font-family:Inter,system-ui,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text)}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
header{border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:24px}
header .name{font-weight:800;font-size:18px;background:linear-gradient(135deg,#e5e7eb,var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
header .sub{color:var(--muted);font-size:13px;margin-top:2px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.stat .v{font-size:24px;font-weight:700;font-family:ui-monospace}
.stat .l{font-size:11px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.5px}
.stat .accent{color:var(--accent)}
.stat .ok{color:var(--ok)}
.stat .warn{color:var(--warn)}
.stat .bad{color:var(--bad)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:24px}
.panel h3{font-size:14px;font-weight:600;margin-bottom:12px}
table{width:100%;border-collapse:collapse}
th,td{padding:6px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:12px;font-family:ui-monospace}
th{color:var(--muted);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
td{color:var(--muted)}
tr:hover{background:rgba(31,42,55,.3)}
.badge{display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px}
.badge.ok{background:rgba(34,197,94,.12);color:var(--ok)}
.badge.reject{background:rgba(239,68,68,.12);color:var(--bad)}
.badge.warn{background:rgba(245,154,11,.12);color:var(--warn)}
code{font-family:ui-monospace;font-size:11px;background:rgba(255,255,255,.04);padding:2px 6px;border-radius:4px}
.btn{background:var(--accent);color:#fff;border:0;padding:8px 16px;border-radius:8px;font-weight:600;cursor:pointer;font-size:13px}
.btn:hover{background:#60a5fa}
.muted{color:var(--muted);font-size:13px}
.chain-ok{color:var(--ok)}
.chain-bad{color:var(--bad)}
</style></head><body>
<div class="wrap">
  <header><div class="name">Recoup</div><div class="sub">Multi-source reconciliation control plane — web dashboard</div></header>

  <div class="stats" id="stats">
    <div class="stat"><div class="v" id="stat-total">0</div><div class="l">Transactions</div></div>
    <div class="stat"><div class="v ok" id="stat-matched">0</div><div class="l">Auto-matched</div></div>
    <div class="stat"><div class="v warn" id="stat-exc">0</div><div class="l">Exceptions</div></div>
    <div class="stat"><div class="v accent" id="stat-rate">0%</div><div class="l">Auto-match rate</div></div>
    <div class="stat"><div class="v" id="stat-events">0</div><div class="l">Audit events</div></div>
    <div class="stat"><div class="v chain-ok" id="stat-chain">✓</div><div class="l">Chain integrity</div></div>
  </div>

  <div class="panel">
    <h3>Audit trail (last 50 events)</h3>
    <button class="btn" style="float:right;margin-top:-28px" onclick="runDemo()">Re-run demo</button>
    <div id="audit"></div>
  </div>

  <div class="panel">
    <h3>Exception breakdown</h3>
    <div id="exceptions"></div>
  </div>
</div>

<script>
async function fetchJSON(url){const r=await fetch(url);if(!r.ok)throw new Error(r.status);return r.json();}

async function runDemo(){
  const btn=document.querySelector('button');btn.disabled=true;btn.textContent="Running pipeline…";
  try{const r=await fetchJSON('/api/run-demo');updateStats(r);}catch(e){btn.textContent="Failed";}
  btn.disabled=false;btn.textContent="Re-run demo";
}

function updateStats(s){
  document.getElementById("stat-total").textContent=s.total;
  document.getElementById("stat-matched").textContent=s.auto_matched;
  document.getElementById("stat-exc").textContent=s.exceptions;
  document.getElementById("stat-rate").textContent=s.auto_match_rate+"%";
  document.getElementById("stat-events").textContent=s.event_count;
  document.getElementById("stat-chain").textContent=s.chain_ok?"✓ Chain intact":"✗ Broken";
  document.getElementById("stat-chain").className=s.chain_ok?"chain-ok":"chain-bad";
}

async function renderAudit(){
  try{
    const events=await fetchJSON('/api/events?limit=50');
    document.getElementById("audit").innerHTML=events.length===0
      ?'<p class="muted">No events yet. Run the demo to populate the audit trail.</p>'
      :'<table><thead><tr><th>#</th><th>Type</th><th>Txn</th><th>Time</th><th>Payload</th></tr></thead><tbody>'+
      events.map(e=>`<tr>
        <td>#${e.seq}</td>
        <td><span class="badge ${e.event_type.includes('reject')||e.event_type.includes('violation')?'reject':'ok'}">${e.event_type}</span></td>
        <td>${e.txn_id||"<span class='muted'>global</span>"}</td>
        <td>${new Date(e.created_at).toLocaleTimeString()}</td>
        <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${JSON.stringify(e.payload).slice(0,120)}</td>
      </tr>`).join("")+"</tbody></table>";
  }catch(e){
    document.getElementById("audit").innerHTML='<p class="muted">Backend not started</p>';
  }
}

async function init(){try{const s=await fetchJSON('/api/stats');updateStats(s);}catch(e){}await renderAudit();setInterval(()=>{renderAudit()},3000);}
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
