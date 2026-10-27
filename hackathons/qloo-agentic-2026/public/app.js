"use strict";
const qs = (x) => document.querySelector(x);
const esc = (s) => String(s==null?"":s).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const form=qs("#queryform"),btn=qs("#go"),notice=qs("#notification");
let lastResult=null;
function info(text,klass) { notice.textContent=text; notice.className="notice "+(klass||""); }
function storyHTML(s) {
 const beats=(s.beats||[]).map(b=>"<li><b>"+esc(b.role)+" · "+esc(b.time)+"</b> — "+esc(b.script)+"</li>").join("");
 const safeUrl=/^https:\/\/[a-z0-9.-]+\//i.test(s.source_url||"")?s.source_url:"#";
 return '<article class="story-card"><span class="topicchip">'+esc(s.theme)+'</span><h3>'+esc(s.hook)+'</h3><p><b>Cultural anchor:</b> '+esc(s.cultural_anchor)+' · '+esc(s.category)+' #'+esc(s.qloo_rank)+'</p><p><b>Evidence-based claim:</b> '+esc(s.claim)+'</p><p><b>Scene direction:</b> '+esc(s.visual)+'</p><a href="'+esc(safeUrl)+'" target="_blank" rel="noopener noreferrer">'+esc(s.source)+' ↗</a><details><summary>VIEW 45-SECOND STORYBOARD</summary><ol>'+beats+'</ol></details></article>';
}
function show(data) {
 lastResult=data;
 qs("#results-intro").textContent="Live taste graph match: "+data.seed.name+" · "+data.affinities.length+" Qloo cultural entities. Ranks shown within each API result category.";
 qs("#live-tag").textContent="LIVE QLOO · "+data.elapsed_ms+" MS";
 const results=qs("#affinities");
 results.className="affinities";
 results.innerHTML=data.affinities.map(item=>'<article class="affinity"><div class="top"><span>'+esc(item.category.toUpperCase())+'</span><span>#'+esc(item.rank)+' IN CATEGORY</span></div><h3>'+esc(item.name)+'</h3><small>Qloo taste graph · returned entity</small></article>').join("");
 qs("#stories").innerHTML=data.stories.map(storyHTML).join("");
 const trace=qs("#trace");
 trace.innerHTML='<span class="sectiontag">LIVE AGENT TRACE · '+esc(data.api_calls)+'</span><ol>'+data.trace.map(x=>'<li><strong>'+esc(x.agent)+'</strong> — '+esc(x.detail)+'</li>').join('')+'</ol><p>'+esc(data.notice)+'</p>';
 qs("#export").disabled=false;
 info("Live Qloo insight run completed. Cultural affinities and source-linked science briefs are ready. "+(data.warnings||[]).join(" "),"success");
 qs("#results-section").scrollIntoView({behavior:"smooth",block:"start"});
}
async function check() {
 try {
   const r=await fetch("/api/health",{cache:"no-store"});
   const j=await r.json();
   let ready=j.mode==="qloo_live_ready";
   qs("#status").textContent=ready?"LIVE QLOO ENGINE":"API KEY PENDING";
   qs("#status").className="pill live-indicator "+(ready?"ready":"pending");
   if(ready) info("Qloo API key configured. Choose a movie, artist, book, game or show to run real cultural intelligence.","success");
 } catch(_) {
   qs("#status").textContent="SERVICE CHECK FAILED";
   info("Could not confirm service status. Refresh later.","error");
 }
}
form.addEventListener("submit",async(ev)=>{
 ev.preventDefault();
 const seed=qs("#seed").value.trim(),type=qs("#type").value;
 if(seed.length<2)return info("Please enter a valid title or artist.","error");
 btn.disabled=true;btn.innerHTML="RESEARCHING <span>…</span>";
 info("Taste Scout is resolving your cultural anchor. Real Qloo insights may take a few seconds.");
 try {
  const r=await fetch("/api/curate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({seed,type})});
  const j=await r.json();
  if(!r.ok||!j.live)throw Error(j.error||"No live Qloo results available.");
  show(j);
 } catch(e) {
  info(e.message||"Live Qloo API was unavailable.","error");
 } finally {
  btn.disabled=false;btn.innerHTML="EXPLORE AFFINITIES <span>↗</span>";
 }
});
document.querySelectorAll("[data-seed]").forEach(btn=>btn.addEventListener("click",()=>{
 qs("#seed").value=btn.dataset.seed;
 qs("#type").value=btn.dataset.type;
 qs("#seed").focus();
}));
qs("#export").addEventListener("click",()=>{
 if(!lastResult)return;
 const file={project:"SIAS Taste-to-Science",origin:"Live Qloo Hackathon API",seed:lastResult.seed,
  taste_affinities:lastResult.affinities,storyboards:lastResult.stories,agent_trace:lastResult.trace,
  scientific_review_note:lastResult.notice};
 const blob=new Blob([JSON.stringify(file,null,2)],{type:"application/json"});
 const url=URL.createObjectURL(blob);
 const a=document.createElement("a");a.href=url;a.download="SIAS-Qloo-Storyboards.json";
 document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
check();
