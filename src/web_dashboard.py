"""Web dashboard for Recoup — S++ edition.

Per-decision explain view, per-txn timeline, eval panel with precision/recall/F1.
Zero-dependency: Python's built-in http.server.
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
PIPELINE_DB = str(Path(__file__).resolve().parent.parent / ".recoup" / "pipeline.db")


def _ensure_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def _init_pipeline_db():
    """Create tables for storing pipeline decisions and eval results."""
    Path(PIPELINE_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PIPELINE_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS match_decisions (
            txn_id_a TEXT, txn_id_b TEXT, matched INTEGER, source TEXT,
            reasons TEXT, score REAL, score_breakdown TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS eval_results (
            run_id TEXT PRIMARY KEY, total INTEGER, true_positives INTEGER,
            false_positives INTEGER, false_negatives INTEGER,
            precision REAL, recall REAL, f1 REAL, ground_truth_count INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.close()


def run_demo_pipeline():
    """Run reconciliation on 3000 txns, persist events + decisions + eval."""
    _ensure_db()
    _init_pipeline_db()

    # Reset event store
    if Path(DB_PATH).exists():
        Path(DB_PATH).unlink()

    store = EventStore(DB_PATH)
    txns, ground_truth = generate(total_transactions=3000, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())

    # Log events
    for d in result.rule_decisions:
        if d.matched:
            store.append("rule_match", {
                "a": d.txn_id_a, "b": d.txn_id_b,
                "score": d.score, "reasons": d.reasons,
            }, d.txn_id_a)
    for d in result.prob_decisions:
        if d.matched:
            store.append("prob_match", {
                "a": d.txn_id_a, "b": d.txn_id_b,
                "score": d.score, "reasons": d.reasons,
                "breakdown": d.score_breakdown,
            }, d.txn_id_a)
    for exc in result.exceptions:
        store.append("exception_classified", {
            "reason": exc.reason.value, "reference": exc.reference,
        }, exc.txn_id)
    for diag in result.diagnoses:
        store.append("llm_diagnosis", {
            "txn_id": diag.txn_id, "class": diag.diagnosis_class.value,
            "confidence": diag.confidence, "refused": diag.refused,
        }, diag.txn_id)

    chain_ok, _ = store.verify_chain()

    # Store decisions for explain view
    pconn = sqlite3.connect(PIPELINE_DB)
    pconn.execute("DELETE FROM match_decisions")
    for d in result.rule_decisions + result.prob_decisions:
        pconn.execute(
            "INSERT INTO match_decisions (txn_id_a, txn_id_b, matched, source, reasons, score, score_breakdown) VALUES (?,?,?,?,?,?,?)",
            (d.txn_id_a, d.txn_id_b, int(d.matched), d.source.value,
             json.dumps(d.reasons), d.score, json.dumps(d.score_breakdown)),
        )
    pconn.commit()

    # Eval: compute precision/recall/F1 against ground truth
    gt_pairs = set()
    gt_exception_pairs = set()
    for a, b, label in ground_truth:
        if label.startswith("exception:"):
            gt_exception_pairs.add((a, b))
        else:
            gt_pairs.add((min(a, b), max(a, b)))

    engine_matched = set()
    for d in result.rule_decisions + result.prob_decisions:
        if d.matched:
            pair = (min(d.txn_id_a, d.txn_id_b), max(d.txn_id_a, d.txn_id_b))
            engine_matched.add(pair)

    true_positives = len(engine_matched & gt_pairs)
    false_positives = len(engine_matched - gt_pairs)
    false_negatives = len(gt_pairs - engine_matched)
    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(true_positives + false_negatives, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)

    import uuid
    run_id = "eval_" + uuid.uuid4().hex[:8]
    pconn.execute(
        "INSERT INTO eval_results (run_id, total, true_positives, false_positives, false_negatives, precision, recall, f1, ground_truth_count) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, len(txns), true_positives, false_positives, false_negatives,
         round(precision, 4), round(recall, 4), round(f1, 4), len(gt_pairs)),
    )
    pconn.commit()
    pconn.close()

    return {
        "total": result.report.total,
        "auto_matched": result.report.rule_matched + result.report.prob_matched,
        "exceptions": result.report.exceptions,
        "diagnosed": result.report.diagnosed,
        "refused": result.report.refused,
        "auto_match_rate": round(result.report.auto_match_rate, 1),
        "exception_rate": round(result.report.exception_rate, 1),
        "chain_ok": chain_ok,
        "event_count": len(store.get_all_events()),
        "eval": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "ground_truth_count": len(gt_pairs),
        },
    }


def fetch_events(limit=50):
    store = EventStore(DB_PATH)
    events = store.get_all_events()
    return events[-limit:][::-1]


def fetch_txn_events(txn_id):
    store = EventStore(DB_PATH)
    return store.get_events_for_txn(txn_id)


def fetch_explain(txn_id_a, txn_id_b):
    """Fetch score breakdown for a matched pair."""
    _init_pipeline_db()
    conn = sqlite3.connect(PIPELINE_DB)
    row = conn.execute(
        "SELECT score, score_breakdown, reasons, source FROM match_decisions WHERE (txn_id_a=? AND txn_id_b=?) OR (txn_id_a=? AND txn_id_b=?)",
        (txn_id_a, txn_id_b, txn_id_b, txn_id_a),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "score": row[0],
        "breakdown": json.loads(row[1]) if row[1] else {},
        "reasons": json.loads(row[2]) if row[2] else [],
        "source": row[3],
    }


def fetch_eval():
    """Get latest eval results."""
    _init_pipeline_db()
    conn = sqlite3.connect(PIPELINE_DB)
    row = conn.execute(
        "SELECT run_id, total, true_positives, false_positives, false_negatives, precision, recall, f1, ground_truth_count, created_at FROM eval_results ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "run_id": row[0], "total": row[1], "true_positives": row[2],
        "false_positives": row[3], "false_negatives": row[4],
        "precision": row[5], "recall": row[6], "f1": row[7],
        "ground_truth_count": row[8], "created_at": row[9],
    }


HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Recoup — Reconciliation Control Plane</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/house-ui.css">
<style>
  .breakdown-bar{display:flex;align-items:center;gap:6px;margin:3px 0}
  .breakdown-bar .bar{height:8px;border-radius:4px;transition:width .3s}
  .breakdown-bar .label{font-family:var(--mono);font-size:12px;min-width:100px}
  .breakdown-bar .val{font-family:var(--mono);font-size:12px;min-width:40px;text-align:right}
  .timeline-step{display:flex;align-items:flex-start;gap:12px;margin:8px 0;padding:8px 12px;border-left:3px solid var(--border);border-radius:0 6px 6px 0;background:var(--surface)}
  .timeline-step.active{border-left-color:var(--accent)}
  .timeline-step .ts-time{font-family:var(--mono);font-size:11px;color:var(--muted);min-width:80px}
  .timeline-step .ts-type{font-weight:600;font-size:13px}
  .timeline-step .ts-detail{font-size:12px;color:var(--muted);margin-top:2px}
  .eval-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .eval-card{text-align:center;padding:16px;border-radius:8px;background:var(--surface);border:1px solid var(--border)}
  .eval-card .ev{font-size:28px;font-weight:800;font-family:var(--mono)}
  .eval-card .el{font-size:12px;color:var(--muted);margin-top:4px}
  .eval-card.good .ev{color:var(--green)}
  .eval-card.warn .ev{color:var(--amber)}
  .eval-card.bad .ev{color:var(--red)}
  .modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;justify-content:center;align-items:center}
  .modal-overlay.open{display:flex}
  .modal{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto}
  .modal h3{margin-top:0}
  .modal .close{float:right;cursor:pointer;font-size:18px;color:var(--muted)}
  .modal .close:hover{color:var(--text)}
  .clickable-row{cursor:pointer}
  .clickable-row:hover{background:var(--surface)}
</style>
</head><body>
<header class="topbar"><div class="wrap">
  <a class="brand" href="/"><span class="mark">◈</span> Recoup</a>
  <nav class="topnav"><a href="#pipeline" class="active">Pipeline</a><a href="#eval-panel">Eval</a><a href="#audit-panel">Audit</a><a href="#exceptions-panel">Exceptions</a><a href="https://github.com/HarshCodeK/recoup">GitHub</a></nav>
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
      <div class="pstep done"><div class="node">◈</div><div class="lbl">Probability</div><div class="desc">feature scoring</div></div>
      <div class="pstep done"><div class="node">✋</div><div class="lbl">Triage</div><div class="desc">LLM · can refuse</div></div>
      <div class="pstep done"><div class="node">🧾</div><div class="lbl">Audit</div><div class="desc">hash-chained</div></div>
    </div>
    <div class="card"><h3>▶ Demo <span class="spacer"></span><button class="btn sm" id="demo-btn" onclick="runDemo()">Re-run 3,000-txn pipeline</button></h3>
      <p class="muted">Regenerates the deterministic dataset (seed 42), reruns rule → probability → classify → diagnose, rebuilds the chain.</p>
    </div>
  </section>

  <section id="eval-panel">
    <h2>Evaluation</h2>
    <p class="sub">Precision / Recall / F1 against ground truth. The generator knows which pairs should match.</p>
    <div class="card">
      <div class="eval-grid" id="eval-grid">
        <div class="eval-card"><div class="ev" id="eval-prec">—</div><div class="el">Precision</div></div>
        <div class="eval-card"><div class="ev" id="eval-rec">—</div><div class="el">Recall</div></div>
        <div class="eval-card"><div class="ev" id="eval-f1">—</div><div class="el">F1 Score</div></div>
      </div>
      <div id="eval-detail" style="margin-top:12px;font-size:13px;color:var(--muted)"></div>
    </div>
  </section>

  <section id="audit-panel">
    <h2>Audit trail</h2>
    <p class="sub">Last 50 events, live. Click any row to inspect its payload or see the decision breakdown.</p>
    <div class="card"><h3>◈ Events <span class="spacer"></span><span class="pill ok" id="chain-pill">chain: checking…</span></h3>
      <div id="audit"></div>
    </div>
  </section>

  <section id="exceptions-panel">
    <h2>Exception breakdown</h2>
    <p class="sub">What the engines could not auto-match, by reason.</p>
    <div class="card"><h3>⚠ Queue</h3><div id="exceptions"></div></div>
  </section>
</div>

<!-- Explain modal -->
<div class="modal-overlay" id="explain-modal">
  <div class="modal">
    <span class="close" onclick="closeModal('explain-modal')">&times;</span>
    <h3>Score Breakdown</h3>
    <div id="explain-content"></div>
  </div>
</div>

<!-- Timeline modal -->
<div class="modal-overlay" id="timeline-modal">
  <div class="modal">
    <span class="close" onclick="closeModal('timeline-modal')">&times;</span>
    <h3>Transaction Timeline</h3>
    <div id="timeline-content"></div>
  </div>
</div>

<footer><div class="wrap"><a href="https://github.com/HarshCodeK/recoup">github.com/HarshCodeK/recoup</a><span style="float:right" class="muted">MIT · deterministic money math</span></div></footer>

<script>
async function fetchJSON(url){const r=await fetch(url);if(!r.ok)throw new Error(r.status);return r.json();}

function closeModal(id){document.getElementById(id).classList.remove('open');}
function openModal(id){document.getElementById(id).classList.add('open');}

async function runDemo(){
  const btn=document.getElementById('demo-btn');btn.disabled=true;btn.innerHTML='<span class="spin"></span> Running pipeline…';
  try{const r=await fetchJSON('/api/run-demo');updateStats(r);if(r.eval)updateEval(r.eval);await renderAudit();}catch(e){btn.textContent="Failed — retry";}
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
    exc.innerHTML='<div style="display:flex;gap:14px;align-items:center"><div style="font-family:var(--mono);font-size:26px">'+s.exceptions+'</div>'+
    '<div class="muted">open exceptions · '+(s.refused||0)+' LLM refusals (safe) · match rate '+s.auto_match_rate+'%</div></div>';
  }
}

function updateEval(e){
  const pClass=e.precision>=0.9?'good':e.precision>=0.7?'warn':'bad';
  const rClass=e.recall>=0.9?'good':e.recall>=0.7?'warn':'bad';
  const fClass=e.f1>=0.9?'good':e.f1>=0.7?'warn':'bad';
  document.getElementById("eval-prec").textContent=(e.precision*100).toFixed(1)+"%";
  document.getElementById("eval-prec").parentElement.className="eval-card "+pClass;
  document.getElementById("eval-rec").textContent=(e.recall*100).toFixed(1)+"%";
  document.getElementById("eval-rec").parentElement.className="eval-card "+rClass;
  document.getElementById("eval-f1").textContent=(e.f1*100).toFixed(1)+"%";
  document.getElementById("eval-f1").parentElement.className="eval-card "+fClass;
  document.getElementById("eval-detail").innerHTML=
    e.ground_truth_count+" ground-truth pairs · "+e.true_positives+" true positives · "+
    e.false_positives+" false positives · "+e.false_negatives+" false negatives";
}

async function showExplain(txnId){
  // Fetch events for this txn to find matched pairs
  try{
    const events=await fetchJSON('/api/txn?id='+txnId);
    // Find match events
    let pairFound=false;
    for(const ev of events){
      const p=ev.payload;
      if((ev.event_type==='rule_match'||ev.event_type==='prob_match')&&p.a&&p.b){
        const other=p.a===txnId?p.b:p.a;
        const explain=await fetchJSON('/api/txn-explain?a='+txnId+'&b='+other);
        if(explain){
          pairFound=true;
          renderExplain(txnId,other,explain);
          break;
        }
      }
    }
    if(!pairFound){
      document.getElementById('explain-content').innerHTML='<p class="muted">No matched pair found for this transaction. It may be an exception.</p>';
      openModal('explain-modal');
    }
  }catch(e){
    document.getElementById('explain-content').innerHTML='<p class="muted">Could not load explanation.</p>';
    openModal('explain-modal');
  }
}

function renderExplain(a,b,ex){
  const bd=ex.breakdown||{};
  const rows=[
    {label:'Amount',val:bd.amount||0,strong:bd.amount_strong,color:'var(--green)'},
    {label:'Date',val:bd.date||0,strong:false,color:'var(--blue)'},
    {label:'Reference',val:bd.ref||0,strong:bd.ref_strong,color:'var(--amber)'},
    {label:'Counterparty',val:bd.counterparty||0,strong:false,color:'var(--purple,#9b59b6)'},
  ];
  let html='<div style="margin-bottom:12px"><span class="pill '+(ex.source==='rule'?'ok':'blue')+'">'+ex.source+'</span> '+
    '<span class="mono" style="font-size:12px">'+a+' ↔ '+b+'</span></div>';
  html+='<div style="font-size:13px;color:var(--muted);margin-bottom:8px">Score: <b style="color:var(--text)">'+(ex.score*100).toFixed(1)+'%</b> · Reasons: '+ex.reasons.join(', ')+'</div>';
  for(const r of rows){
    const pct=Math.round(r.val*100);
    html+='<div class="breakdown-bar"><span class="label">'+r.label+'</span>'+
      '<div style="flex:1;background:var(--border);border-radius:4px;height:8px"><div class="bar" style="width:'+pct+'%;background:'+r.color+'"></div></div>'+
      '<span class="val">'+pct+'%</span>'+
      (r.bold?'<span class="pill ok" style="font-size:10px">strong</span>':'')+'</div>';
  }
  document.getElementById('explain-content').innerHTML=html;
  openModal('explain-modal');
}

async function showTimeline(txnId){
  try{
    const events=await fetchJSON('/api/txn?id='+txnId);
    if(events.length===0){
      document.getElementById('timeline-content').innerHTML='<p class="muted">No events found for this transaction.</p>';
      openModal('timeline-modal');
      return;
    }
    let html='<div style="margin-bottom:8px"><span class="mono" style="font-size:13px">'+txnId+'</span> · '+events.length+' events</div>';
    for(const ev of events){
      const p=ev.payload;
      let detail='';
      if(ev.event_type==='rule_match') detail='Matched with '+p.b+' · '+p.reasons?.join(', ');
      else if(ev.event_type==='prob_match') detail='Matched with '+p.b+' · score='+(p.score*100).toFixed(1)+'% · '+p.reasons?.join(', ');
      else if(ev.event_type==='exception_classified') detail='Reason: '+p.reason;
      else if(ev.event_type==='llm_diagnosis') detail='Class: '+p.class+' · confidence='+(p.confidence*100).toFixed(0)+'%'+(p.refused?' · REFUSED':'');
      else if(ev.event_type==='ingest') detail='Source: '+p.source+' · amount='+p.amount_paise+'p';
      else detail=JSON.stringify(p).slice(0,120);
      html+='<div class="timeline-step"><div class="ts-time">'+new Date(ev.created_at).toLocaleTimeString()+'</div><div>'+
        '<div class="ts-type"><span class="pill '+(ev.event_type.includes('exception')||ev.event_type.includes('refused')?'bad':'ok')+'">'+ev.event_type+'</span></div>'+
        '<div class="ts-detail">'+detail+'</div></div></div>';
    }
    document.getElementById('timeline-content').innerHTML=html;
    openModal('timeline-modal');
  }catch(e){
    document.getElementById('timeline-content').innerHTML='<p class="muted">Could not load timeline.</p>';
    openModal('timeline-modal');
  }
}

function showTxn(seq,type,txn,time,payload){
  // If it's a match event, show explain; otherwise show timeline
  if(type==='rule_match'||type==='prob_match'){
    showExplain(txn);
  }else{
    showTimeline(txn);
  }
}

async function renderAudit(){
  try{
    const events=await fetchJSON('/api/events?limit=50');
    document.getElementById("audit").innerHTML=events.length===0
      ?'<p class="empty">No events yet. Run the demo to populate the audit trail.</p>'
      :'<table class="hu"><thead><tr><th>#</th><th>Type</th><th>Txn</th><th>Time</th><th>Payload</th></tr></thead><tbody>'+
      events.map(e=>{
        const bad=/reject|violation|refus|fail/.test(e.event_type);
        const full=JSON.stringify(e.payload).replace(/</g,'&lt;');
        const txnLink=e.txn_id?'<a href="#" onclick="event.preventDefault();showTimeline(\''+e.txn_id+'\')" class="mono">'+e.txn_id+'</a>':"<span class='muted'>global</span>";
        return'<tr class="clickable-row" onclick=\'showTxn('+e.seq+',"'+e.event_type+'","'+(e.txn_id||'')+'","'+new Date(e.created_at).toLocaleTimeString()+'","'+full.slice(0,400)+'")\'>'+
        '<td class="muted">#'+e.seq+'</td>'+
        '<td><span class="pill '+(bad?'bad':'ok')+'">'+e.event_type+'</span></td>'+
        '<td>'+txnLink+'</td>'+
        '<td class="muted">'+new Date(e.created_at).toLocaleTimeString()+'</td>'+
        '<td class="muted" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+full.slice(0,110)+'</td>'+
      '</tr>';}).join("")+"</tbody></table>";
  }catch(e){
    document.getElementById("audit").innerHTML='<p class="empty">Dashboard server not running.</p>';
  }
}

async function init(){
  try{
    const s=await fetchJSON('/api/stats');
    updateStats(s);
    const ev=await fetchJSON('/api/eval');
    if(ev)updateEval(ev);
  }catch(e){}
  await renderAudit();
  setInterval(async()=>{
    try{const s=await fetchJSON('/api/stats');updateStats(s);}catch(e){}
    try{const ev=await fetchJSON('/api/eval');if(ev)updateEval(ev);}catch(e){}
    await renderAudit();
  },4000);
}
init();
</script>
</body></html>"""


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
        elif path == "/api/txn-explain":
            a = qs.get("a", [""])[0]
            b = qs.get("b", [""])[0]
            result = fetch_explain(a, b)
            self._send_json(result if result else {"error": "not found"})
        elif path == "/api/eval":
            result = fetch_eval()
            self._send_json(result if result else {})
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
        matched = sum(1 for e in events if e["event_type"] in ("rule_match", "prob_match"))
        exc = sum(1 for e in events if e["event_type"] == "exception_classified")
        refused = sum(1 for e in events if e["event_type"] == "llm_diagnosis" and e.get("payload", {}).get("refused"))
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
    _init_pipeline_db()
    port = int(__import__("os").environ.get("PORT", "8300"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"\n  Recoup dashboard on http://localhost:{port}")
    print(f"   Run /api/run-demo to generate 3,000 transactions")
    print()
    server.serve_forever()


if __name__ == "__main__":
    main()
