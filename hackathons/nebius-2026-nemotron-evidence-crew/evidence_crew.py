"""SIAS independent science claim auditor using NVIDIA Nemotron on Nebius Token Factory."""
import argparse,datetime,json,os,re,time,urllib.request,urllib.error
from pathlib import Path
BASE="https://api.tokenfactory.nebius.com/v1"
NANO="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
SUPER="nvidia/nemotron-3-super-120b-a12b"
ALLOWED={"supported","contradicted","insufficient_evidence"}
SYSTEM="""You are the independent SIAS scientific claim-verification agent.
Use ONLY supplied source excerpts, not outside facts.
Do not invent source titles, URLs or factual claims.
If sources do not establish the claim, verdict MUST be insufficient_evidence.
Never approve unsupported numeric examples as scientific facts.
Return JSON ONLY, with keys: verdict (supported|contradicted|insufficient_evidence),
confidence (number 0..1), cited_source_ids (array of integers),
explanation (string), suggested_revision (string)."""

def read_case(path):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data,dict) or not str(data.get("claim","")).strip():
        raise ValueError("A nonempty scientific claim is required")
    if not isinstance(data.get("sources"),list) or not 1<=len(data["sources"])<=20:
        raise ValueError("Provide 1..20 reference excerpts")
    for item in data["sources"]:
        if not isinstance(item,dict) or not str(item.get("excerpt","")).strip():
            raise ValueError("Every source requires an excerpt")
    return data

def messages(case):
    src=[{"id":i+1,"title":str(s.get("title",""))[:180],"url":str(s.get("url",""))[:280],
          "excerpt":str(s["excerpt"])[:1200]} for i,s in enumerate(case["sources"])]
    return [{"role":"system","content":SYSTEM},
      {"role":"user","content":json.dumps({"claim":str(case["claim"])[:1500],"sources":src},ensure_ascii=False)}]

def validate(x,n):
    if not isinstance(x,dict) or x.get("verdict") not in ALLOWED:raise ValueError("Invalid verdict")
    confidence=x.get("confidence")
    if isinstance(confidence,bool) or not isinstance(confidence,(float,int)) or not 0<=confidence<=1:
        raise ValueError("Invalid confidence")
    ids=x.get("cited_source_ids")
    if not isinstance(ids,list) or any(type(i)!=int or not 1<=i<=n for i in ids):
        raise ValueError("Invalid source IDs")
    for field in ("explanation","suggested_revision"):
        if not isinstance(x.get(field),str) or not x[field].strip():raise ValueError("Missing "+field)
    return {"verdict":x["verdict"],"confidence":float(confidence),"cited_source_ids":sorted(set(ids)),
      "explanation":x["explanation"][:1200],"suggested_revision":x["suggested_revision"][:1200]}

def call_nebius(payload,model=NANO,timeout=60):
    if model not in (NANO,SUPER):raise ValueError("Unsupported NVIDIA model")
    key=os.getenv("NEBIUS_API_KEY") or os.getenv("NEBIUS_TOKEN_FACTORY_KEY")
    if not key:raise RuntimeError("NEBIUS_API_KEY is missing; no inference was performed")
    base=os.getenv("NEBIUS_BASE_URL",BASE).rstrip("/")
    if not base.startswith("https://") or not base.split("/")[2].endswith(".nebius.com"):
        raise ValueError("Only HTTPS Nebius Token Factory endpoint is supported")
    data=json.dumps({"model":model,"messages":payload,"temperature":0,"max_tokens":500}).encode()
    req=urllib.request.Request(base+"/chat/completions",data=data,method="POST",
       headers={"Content-Type":"application/json","Authorization":"Bearer "+key})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return json.loads(response.read(180000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Nebius Token Factory HTTP "+str(exc.code)+
          "; check billing, model access and API key") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Nebius network connection failed") from exc

def parse_json_content(s):
    if not isinstance(s,str) or not s.strip():raise ValueError("Empty model response")
    s=s.strip()
    # Strip optional Markdown code-fence lines without exposing model data.
    if s.startswith(chr(96)*3):s="\n".join(s.splitlines()[1:-1])
    try:return json.loads(s)
    except json.JSONDecodeError:
        a=s.find("{");b=s.rfind("}")
        if a<0 or b<a:raise ValueError("Model reply is not JSON")
        return json.loads(s[a:b+1])

def evaluate(case,model=NANO):
    start=time.perf_counter()
    reply=call_nebius(messages(case),model=model)
    ms=round((time.perf_counter()-start)*1000)
    choices=reply.get("choices") or []
    if not choices:raise ValueError("Empty model choices")
    verdict=validate(parse_json_content(choices[0].get("message",{}).get("content")),len(case["sources"]))
    use=reply.get("usage") or {}
    return {"provider":"Nebius Token Factory","model":model,"claim":case["claim"],"verdict":verdict,
      "latency_ms":ms,"response_id":reply.get("id"),
      "usage":{"prompt_tokens":use.get("prompt_tokens"),"completion_tokens":use.get("completion_tokens"),
               "total_tokens":use.get("total_tokens")},
      "timestamp_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",default=str(Path(__file__).with_name("sample_case.json")))
    p.add_argument("--model",choices=("nano","super"),default="nano")
    p.add_argument("--dry-run",action="store_true")
    p.add_argument("--output")
    args=p.parse_args()
    case=read_case(args.case)
    model=NANO if args.model=="nano" else SUPER
    result=({"model":model,"api_called":False,"claim":case["claim"],"source_count":len(case["sources"])}
            if args.dry_run else evaluate(case,model))
    out=json.dumps(result,indent=2,ensure_ascii=False)
    if args.output:
        f=Path(args.output);f.parent.mkdir(parents=True,exist_ok=True);f.write_text(out+"\n")
    print(out)
if __name__=="__main__":main()
