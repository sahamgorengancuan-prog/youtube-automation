#!/usr/bin/env python3
"""Isolated Meta hackathon judge preview. Offline mode never calls AI.
Real inference is CLI-only (reviewer_eval.py) to avoid a public paid endpoint.
"""
from __future__ import annotations
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = json.loads((ROOT / "fixtures" / "sky.json").read_text())
MAX_BODY = 4096

def preview(question=None):
    """A clearly declared scripted preview; NOT model output."""
    question = question or FIXTURE["question"]
    if not isinstance(question, str) or not 1 <= len(question) <= 250:
        return {"status": "BLOCK", "reason": "Invalid question"}
    if question.strip() != FIXTURE["question"]:
        return {"status": "BLOCK", "reason": "Only the original cited fixture is available in offline mode. Live mode required for new questions."}
    story = {
        "hook": "Why is the sky blue—but sunsets red?",
        "scenes": [
            {"visual":"Sunlight entering Earth's atmosphere, with shorter wavelengths highlighted",
             "narration":"Sunlight contains many colors. Air molecules scatter shorter wavelengths more efficiently.",
             "evidence_id":"S1"},
            {"visual":"Low Sun and longer path through atmosphere, with blue scattered away",
             "narration":"Near sunset, sunlight travels farther through the atmosphere. More blue light is scattered away from our direct view.",
             "evidence_id":"S1"}
        ]}
    return {
        "status":"PREVIEW_ONLY", "model_used":False, "meta_inference_calls":0,
        "question":question,
        "research":{"status":"CITED_FIXTURE","source_excerpts":FIXTURE["source_excerpts"]},
        "director":{"status":"SCRIPTED_OFFLINE_EXAMPLE","storyboard":story},
        "critic":{"status":"SCRIPTED_OFFLINE_EXAMPLE","needs_revision":False,"notes":["Evidence IDs present; scientific accuracy still requires expert evaluation."]},
        "provenance":{"all_scene_refs_valid":True,"scene_refs":["S1","S1"],"known_ids":["S1"]},
        "warning":"Offline fixture. This is not Meta output or evidence of Meta Model API integration."
    }

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Science Director | SimpliSains</title>
<style>
:root{--ink:#132136;--muted:#66758b;--navy:#0c182b;--blue:#356cf0;--line:#dce4ee;--bg:#f5f8fc;--ok:#11725e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1120px;padding:40px 24px 72px;margin:auto}
.header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:40px}.brand{font-weight:800;letter-spacing:-.6px;font-size:22px}.brand span{color:var(--blue)}
.pill{border:1px solid #b7c8dc;color:#4e647d;background:#fff;border-radius:100px;padding:6px 13px;font-size:12px;font-weight:700}
.hero{padding:44px 48px;border-radius:28px;background:var(--navy);color:white;position:relative;overflow:hidden}.hero:after{content:"";position:absolute;width:370px;height:370px;right:-110px;top:-100px;border:75px solid rgba(82,119,225,.12);border-radius:50%;pointer-events:none}
.eyebrow{color:#b5cdff;text-transform:uppercase;letter-spacing:2px;font-size:12px;font-weight:800}.hero h1{font-size:clamp(36px,5.6vw,64px);letter-spacing:-2.5px;line-height:1.05;max-width:750px;margin:16px 0}
.hero p{color:#c5d1e3;max-width:650px;font-size:17px}.alert{margin:24px 0;border-left:4px solid #dca34b;padding:14px 20px;background:#fffaf0;border-radius:8px;color:#725015}
.workspace{padding:28px;border-radius:20px;background:white;border:1px solid var(--line);margin-top:26px}
label{display:block;font-size:13px;font-weight:700;color:var(--muted);margin-bottom:9px}textarea{width:100%;padding:16px 18px;border:1px solid #cad6e5;border-radius:12px;font:inherit;min-height:82px;resize:vertical;background:#fcfdff;color:var(--ink)}
.row{display:flex;gap:15px;align-items:center;flex-wrap:wrap;margin-top:16px}button{background:var(--blue);color:white;font:700 14px system-ui;padding:13px 20px;border:0;border-radius:11px;cursor:pointer}button:hover{background:#2355d9}
.helper{color:var(--muted);font-size:13px}.stages{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:25px}
.stage{background:white;border:1px solid var(--line);border-radius:18px;padding:26px;min-height:214px}.num{color:var(--blue);font-weight:900;font-size:12px;letter-spacing:1.6px}.stage h3{margin:12px 0 7px;font-size:21px;letter-spacing:-.5px}.stage p{color:var(--muted);margin:0}
.results{background:white;border:1px solid var(--line);border-radius:18px;padding:25px;margin-top:24px}.results h2{margin:0 0 18px;font-size:21px}
.scene{border-left:3px solid #7aa6ff;padding:2px 15px;margin:14px 0}.scene strong{display:block}.scene span{color:var(--muted)}.foot{font-size:12px;color:var(--muted);margin-top:26px}
@media(max-width:700px){.hero{padding:30px 26px}.stages{grid-template-columns:1fr}.header{flex-direction:column;align-items:flex-start}}
</style></head>
<body><main><div class="header"><div class="brand">SIMPLI<span>SAINS</span> / SCIENCE DIRECTOR</div><div class="pill">Isolated Hackathon Prototype · Offline Preview</div></div>
<section class="hero"><div class="eyebrow">Evidence-first AI storytelling</div><h1>Every scene should earn its science.</h1>
<p>A research → director → independent critic workflow for creating visual science explanations with traceable evidence. Designed for Meta Muse Spark integration.</p></section>
<div class="alert"><strong>Transparency:</strong> This preview uses original deterministic fixtures, not Meta inference. A completed live Muse Spark evaluation is still required before claiming model integration.</div>
<section class="workspace"><label for="q">Science question</label><textarea id="q">Why is the sky blue, but sunsets often look red?</textarea>
<div class="row"><button id="run">Run offline science review</button><span class="helper" id="status">No paid API calls. Runs on local-only server.</span></div></section>
<section class="stages"><div class="stage"><div class="num">01 / RESEARCH</div><h3>Source-grounded</h3><p>Pass cited scientific excerpts, with stable evidence IDs, into the scene builder.</p></div>
<div class="stage"><div class="num">02 / DIRECTOR</div><h3>Explain visually</h3><p>Translate findings into a short hook and scene-by-scene narration and imagery.</p></div>
<div class="stage"><div class="num">03 / CRITIC</div><h3>Challenge the story</h3><p>Check citation integrity, clarity and potential mismatch between narration and visuals.</p></div></section>
<section id="output" class="results" aria-live="polite"><h2>Trace preview</h2><div class="helper">Run the offline demonstration to see the full sourced scene trace.</div></section>
<div class="foot">© SimpliSains · A prototype of a solo founder. The real production SIAS pipeline is not modified. No source excerpts or evidence are generated during offline preview.</div>
</main>
<script>
const escapeHTML=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const out=document.getElementById('output'),status=document.getElementById('status');
document.getElementById('run').onclick=async()=>{
 status.textContent='Processing offline fixture...';
 try{
 const res=await fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:document.getElementById('q').value})});
 const x=await res.json();
 if(!res.ok){out.innerHTML='<h2>Blocked</h2><p>'+escapeHTML(x.reason)+'</p>';status.textContent='No model call was made';return;}
 let items=x.director.storyboard.scenes.map((s,i)=>'<div class="scene"><strong>Scene '+(i+1)+' · '+escapeHTML(s.evidence_id)+'</strong><div>'+escapeHTML(s.narration)+'</div><span>'+escapeHTML(s.visual)+'</span></div>').join('');
 out.innerHTML='<h2>Trace · '+escapeHTML(x.status)+'</h2><p><strong>Hook:</strong> '+escapeHTML(x.director.storyboard.hook)+'</p>'+items+'<p class="helper"><strong>Critic:</strong> '+escapeHTML(x.critic.notes.join(' '))+'</p><p class="helper"><strong>Integrity:</strong> '+(x.provenance.all_scene_refs_valid?'All scene references match known evidence IDs':'Failed')+'</p>';
 status.textContent='Complete · 0 Meta inference calls · scripted preview';
 }catch(e){status.textContent='Demo unavailable';out.textContent='Local preview failed to respond.';}
}
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_bytes(HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/health":
            self.send_json({"status":"OK", "meta_inference_enabled":False, "deployment":"LOOPBACK_ONLY"})
        else:self.send_error(404)
    def do_POST(self):
        if self.path != "/api/preview":self.send_error(404);return
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>MAX_BODY:raise ValueError("Invalid request size")
            req=json.loads(self.rfile.read(n))
            result=preview(req.get("question"))
            self.send_json(result,200 if result["status"] == "PREVIEW_ONLY" else 422)
        except (ValueError,TypeError,json.JSONDecodeError):
            self.send_json({"status":"BLOCK","reason":"Invalid request"},400)
    def send_json(self,value,status=200):
        self.send_bytes(json.dumps(value,ensure_ascii=False).encode(), "application/json; charset=utf-8",status)
    def send_bytes(self,value,mime,status=200):
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(value)))
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Security-Policy","default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        self.wfile.write(value)
    def log_message(self,format,*args):pass

if __name__=="__main__":
    # No paid API endpoint, and binds ONLY to loopback.
    ThreadingHTTPServer(("127.0.0.1",8891),Handler).serve_forever()
