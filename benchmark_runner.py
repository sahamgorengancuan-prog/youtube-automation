#!/usr/bin/env python3
"""Small controlled NVIDIA Nemotron on Nebius science-verification comparison.
All calls hit real Nebius Token Factory. Curated synthetic test cases, no gold-standard expert rating.
Never prints API credentials; saves result manifests only inside UAT folder.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import json,os,statistics,sys,time,datetime
sys.path.insert(0,str(Path(__file__).resolve().parent))
import evidence_crew as c
B=Path(__file__).resolve().parent
if not os.environ.get("NEBIUS_API_KEY"):
    raise SystemExit("Set NEBIUS_API_KEY securely before running this benchmark.")
cases=[
 {"id":"grav_time_supported","expected":"supported","claim":"A clock deeper in a gravitational field tends to run more slowly than a clock farther away when compared using a common frame.","sources":[{"title":"Gravitational time dilation - test reference excerpt","excerpt":"Clocks that are farther from massive bodies run faster than clocks that are closer to massive bodies. This effect is called gravitational time dilation."}]},
 {"id":"grav_time_contradicted","expected":"contradicted","claim":"In the same gravitational field, clocks nearer a massive body run faster than clocks farther away.","sources":[{"title":"Gravitational time dilation - test reference excerpt","excerpt":"Clocks closer to the massive body run slower relative to clocks far away; their measured elapsed time differs."}]},
 {"id":"unjustified_1000years","expected":"insufficient_evidence","claim":"For every black hole, exactly one hour near its edge always equals 1,000 years on Earth.","sources":[{"title":"General relativity - test reference excerpt","excerpt":"The amount of gravitational time dilation depends on a clock's position within the gravitational field and on the observer's reference frame. A universal fixed conversion is not established in this evidence."}]},
 {"id":"gps_correction","expected":"contradicted","claim":"GPS positioning requires no consideration of relativistic clock-rate corrections.","sources":[{"title":"Satellite navigation - test reference excerpt","excerpt":"Satellite navigation systems such as GPS account for special and general relativistic clock effects to maintain accurate synchronization and positioning."}]},
 {"id":"event_horizon_signals","expected":"supported","claim":"According to a distant observer, a signal emitted close to a black hole's event horizon is gravitationally redshifted.","sources":[{"title":"Gravitational redshift - test reference excerpt","excerpt":"Light moving outward from a strong gravitational field loses frequency as seen by a distant observer; this phenomenon is gravitational redshift."}]},
]
price={c.NANO:(0.06,0.24),c.SUPER:(0.30,0.90)}
import argparse
a=argparse.ArgumentParser();a.add_argument("--variants",default="nano,super");a.add_argument("--cases",type=int,default=5);a.add_argument("--workers",type=int,default=2);a=a.parse_args()
selected=[]
for m in a.variants.split(","):
 selected.append(c.NANO if m=="nano" else c.SUPER if m=="super" else (_ for _ in ()).throw(ValueError(m)))
jobs=[(i,case,model) for i,case in enumerate(cases[:a.cases]) for model in selected]
print("UAT live calls scheduled",len(jobs),"models",selected,"time",datetime.datetime.now().isoformat(),flush=True)
outfolder=B/"results"/"benchmark_external_run";outfolder.mkdir(parents=True,exist_ok=True)
os.chmod(outfolder,0o700)
def run(job):
 i,case,model=job
 t=time.monotonic()
 try:
  r=c.evaluate({"claim":case["claim"],"sources":case["sources"]},model=model)
  usage=r["usage"]; inp=usage.get("prompt_tokens") or 0; gen=usage.get("completion_tokens") or 0
  rt=price[model]
  cost=(inp*rt[0]+gen*rt[1])/1_000_000
  res={"case_id":case["id"],"expected":case["expected"],"actual":r["verdict"]["verdict"],"model":model,
       "confidence":r["verdict"]["confidence"],"cited_source_ids":r["verdict"]["cited_source_ids"],
       "output_valid":True,"expected_label_agreement":r["verdict"]["verdict"]==case["expected"],
       "latency_ms":r["latency_ms"],"tokens":usage,"estimated_cost_usd":round(cost,8),
       "response_id_present":bool(r.get("response_id")),"explanation":r["verdict"]["explanation"][:500],
       "suggested_revision":r["verdict"]["suggested_revision"][:400],"error":None}
 except Exception as e:
  res={"case_id":case["id"],"expected":case["expected"],"actual":None,"model":model,
       "output_valid":False,"expected_label_agreement":False,"latency_ms":round((time.monotonic()-t)*1000),
       "error":type(e).__name__+": "+str(e)[:160],"estimated_cost_usd":None}
 suffix="nano" if model==c.NANO else "super"
 path=outfolder/(str(i+1).zfill(2)+"_"+case["id"]+"_"+suffix+".json")
 path.write_text(json.dumps(res,indent=2,ensure_ascii=False)+"\n")
 os.chmod(path,0o600)
 print(res["case_id"],suffix,res.get("actual"),"valid",res["output_valid"],"latency",res["latency_ms"],
       "tokens",res.get("tokens",{}).get("total_tokens"),"cost",res.get("estimated_cost_usd"),
       "error",res.get("error"),flush=True)
 return res

results=[]
with ThreadPoolExecutor(max_workers=min(max(1,a.workers),2)) as pool:
 futures=[pool.submit(run,j) for j in jobs]
 for f in as_completed(futures):results.append(f.result())
report={"description":"Five-case real Nebius benchmark (requires user-provided credentials). Source excerpts are curated testing fixtures, not external verified citations. Expected labels are simple researcher-authored reference judgments, not a blinded expert panel.","pricing_source":"https://nebius.com/services/token-factory/models/nvidia-nemotron-models-inference","pricing_usd_per_million_tokens":{"nano":{"input":0.06,"output":0.24},"super":{"input":0.30,"output":0.90}},"entries":sorted(results,key=lambda r:(r["case_id"],r["model"])),"models":{}}
for model in selected:
 arr=[r for r in results if r["model"]==model];good=[r for r in arr if r["output_valid"]]
 report["models"][model]={"attempts":len(arr),"successes":len(good),
  "label_agreement_with_curated_cases":round(sum(r["expected_label_agreement"] for r in good)/len(good),3) if good else None,
  "median_latency_seconds":round(statistics.median(r["latency_ms"] for r in good)/1000,2) if good else None,
  "mean_estimated_cost_usd":round(statistics.mean(r["estimated_cost_usd"] for r in good),7) if good else None,
  "measured_total_estimated_spend_usd":round(sum(r["estimated_cost_usd"] for r in good),7)}
p=outfolder/"benchmark_report.json";p.write_text(json.dumps(report,indent=2,ensure_ascii=False));os.chmod(p,0o600)
print("BENCHMARK_REPORT",p,flush=True)
for k,v in report["models"].items():print("SUMMARY",k,json.dumps(v),flush=True)
