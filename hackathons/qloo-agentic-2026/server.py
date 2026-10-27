"""SIAS Taste-to-Science, honest Qloo hackathon live integration."""
import json, os, re, time, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
BASE=os.getenv("QLOO_BASE_URL","https://hackathon.api.qloo.com").rstrip("/")
KEY=os.getenv("QLOO_API_KEY","")
ROOT=Path(__file__).resolve().parent
PORT=int(os.getenv("PORT","18679"))
TYPES={"movie":"urn:entity:movie","artist":"urn:entity:artist","book":"urn:entity:book","video_game":"urn:entity:video_game","tv_show":"urn:entity:tv_show"}
DOMAINS=("urn:entity:movie","urn:entity:artist","urn:entity:book")
LENSES=[
(("space","star","cosmic","moon","orbit","interstellar","astro","galaxy","alien","dune","gravity"),"Space & gravity","Can gravity really bend starlight?","Gravity can bend the path of light, producing gravitational lensing.","NASA Science","https://science.nasa.gov/universe/galaxies/gravitational-lensing/","Animate starlight distorting around a massive object."),
(("music","artist","band","sound","song","jazz","beat","concert","piano","guitar","synth"),"The physics of sound","How do vibrations become your favorite music?","Sound travels as vibrations through a medium, and pitch relates to vibration frequency.","NIDCD — How Do We Hear?","https://www.nidcd.nih.gov/health/how-do-we-hear","Visualize vibrating strings, pressure waves and a responsive ear."),
(("ocean","water","coral","fish","sea","nature","earth","whale","underwater"),"Ocean science","Why does sound behave differently underwater?","Sound travels faster through water than through air.","NOAA — What is sound?","https://oceanservice.noaa.gov/facts/sound.html","Show the same sound pulse moving through air and water."),
(("game","video","matrix","virtual","cyber","visual","color","art","design","animation","cinema"),"Vision & perception","How can still images create the illusion of motion?","A sequence of still images can create apparent motion through visual perception.","Britannica — Phi phenomenon","https://www.britannica.com/science/phi-phenomenon","Reveal still frames turning into continuous apparent motion.")
]
DEFAULT=("Everyday science","Why does light bend in a glass of water?","Light changes direction between materials with different refractive indices.","Britannica — Refraction","https://www.britannica.com/science/refraction","Trace a ray crossing from air into water.")
class QlooError(Exception):
 def __init__(self,status,message):self.status,self.message=status,message
def request_qloo(path,params):
 if not KEY:raise QlooError(503,"Qloo hackathon API key requested, but not yet provisioned. Live insights are unavailable.")
 q=urllib.parse.urlencode(params)
 r=urllib.request.Request(BASE+path+"?"+q,headers={"X-Api-Key":KEY,"Accept":"application/json","User-Agent":"SIAS-Qloo-2026"})
 try:
  with urllib.request.urlopen(r,timeout=14) as res:return json.loads(res.read(1500000))
 except urllib.error.HTTPError as e:raise QlooError(502,"Qloo responded with HTTP "+str(e.code)+".")
 except (urllib.error.URLError,TimeoutError):raise QlooError(502,"Qloo temporarily unreachable.")
def unpack(v):
 if isinstance(v,list):return [x for x in v if isinstance(x,dict)]
 if isinstance(v,dict):
  for parent in (v.get("results"),v):
   if isinstance(parent,list):return [x for x in parent if isinstance(x,dict)]
   if isinstance(parent,dict):
    for k in ("entities","results","items","data"):
     if isinstance(parent.get(k),list):return [x for x in parent[k] if isinstance(x,dict)]
 return []
def ename(x):
 p=x.get("properties") or {}
 return str(x.get("name") or p.get("name") or x.get("title") or p.get("title") or "").strip()[:100]
def eid(x):
 return str(x.get("entity_id") or x.get("id") or x.get("entityId") or "")
def lens(text):
 low=text.lower()
 for keys,*v in LENSES:
  if any(t in low for t in keys):return v
 return DEFAULT
def create(seed,kind):
 st=time.monotonic()
 matches=unpack(request_qloo("/search",{"query":seed,"types":TYPES[kind],"take":5}))
 match=next((e for e in matches if ename(e).lower()==seed.lower()),None) or (matches[0] if matches else None)
 if not match:raise QlooError(404,"No Qloo cultural entity matched. Try a popular movie, musician, book or game.")
 mid=eid(match)
 if not mid:raise QlooError(502,"Qloo search returned an entity without a usable ID.")
 def query(domain):
  return domain,unpack(request_qloo("/v2/insights",{"filter.type":domain,"signal.interests.entities":mid,"take":5,"feature.explainability":"true"}))
 results=[];issues=[]
 with ThreadPoolExecutor(max_workers=3) as ex:
  tasks=[ex.submit(query,d) for d in DOMAINS]
  for f in as_completed(tasks):
   try:
    domain,entities=f.result()
    for index,e in enumerate(entities):
     if not ename(e) or ename(e)==ename(match):continue
     v=(e.get("query") or {}).get("affinity")
     if isinstance(v,dict):v=v.get("score")
     results.append({"name":ename(e),"category":domain.split(":")[-1],"rank":index+1,"affinity":v if isinstance(v,(int,float)) else None,"id":eid(e)})
   except QlooError as e:issues.append(e.message)
 if not results:raise QlooError(502,"Could not obtain live cross-domain Qloo affinities. "+(issues[0] if issues else ""))
 order={"movie":0,"artist":1,"book":2}
 results.sort(key=lambda x:(order.get(x["category"],9),x["rank"]))
 results=results[:12];stories=[]
 for i,item in enumerate(results[:6]):
  theme,hook,claim,source,url,visual=lens(" ".join([seed,ename(match),item["name"],item["category"]]))
  stories.append({"rank":i+1,"cultural_anchor":item["name"],"category":item["category"],"qloo_rank":item["rank"],"theme":theme,"hook":hook,"claim":claim,"source":source,"source_url":url,"visual":visual,
   "beats":[{"time":"00–03","role":"HOOK","script":hook},{"time":"03–12","role":"CULTURAL BRIDGE","script":"Connect "+ename(match)+" to "+item["name"]+" using Qloo's group-level cultural affinity."},{"time":"12–32","role":"SCIENCE EXPLAINED","script":claim},{"time":"32–45","role":"VISUAL PAYOFF","script":visual}]})
 trace=[{"agent":"Taste Scout","detail":"Qloo /search resolved "+ename(match)},{"agent":"Affinity Explorer","detail":"Queried 3 Qloo /v2/insights domains; "+str(len(results))+" cultural entities returned"},{"agent":"Science Architect","detail":"Created "+str(len(stories))+" science video treatments from live affinities"},{"agent":"Evidence Steward","detail":"Attached separate science source references; Qloo is not a science fact checker"}]
 return {"live":True,"seed":{"name":ename(match),"category":kind,"id":mid},"affinities":results,"stories":stories,"trace":trace,"api_calls":"1 /search; 3 /v2/insights attempted","elapsed_ms":round((time.monotonic()-st)*1000),"warnings":issues[:2],"notice":"Group-level Qloo affinities are not individual predictions. Editorial science proposals require source review before publication."}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):print(fmt%args,flush=True)
 def send(self,code,data,content_type="application/json"):
  body=(json.dumps(data,ensure_ascii=False).encode() if content_type=="application/json" else data)
  self.send_response(code)
  for k,v in {"Content-Type":content_type,"Content-Length":str(len(body)),"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Cache-Control":"no-store","Content-Security-Policy":"default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(body)
 def do_GET(self):
  path={"/":"index.html","/index.html":"index.html","/style.css":"style.css","/app.js":"app.js"}.get(self.path)
  if path:
   mt={"index.html":"text/html; charset=utf-8","style.css":"text/css; charset=utf-8","app.js":"application/javascript; charset=utf-8"}[path]
   self.send(200,(ROOT/"public"/path).read_bytes(),mt)
  elif self.path=="/api/health":self.send(200,{"status":"online","mode":"qloo_live_ready" if KEY else "awaiting_qloo_key"})
  else:self.send(404,{"error":"Not found"})
 def do_POST(self):
  if self.path!="/api/curate":return self.send(404,{"error":"Not found"})
  try:
   n=int(self.headers.get("Content-Length","0"))
   if not 1<=n<=4096:return self.send(413,{"error":"Request size invalid"})
   o=json.loads(self.rfile.read(n));seed=str(o.get("seed","")).strip();kind=o.get("type")
   if not 2<=len(seed)<=80 or not re.fullmatch(r"[\w\s\-.,'():!?&/]+",seed) or kind not in TYPES:return self.send(400,{"error":"Enter a valid cultural seed (2–80 characters) and category."})
   self.send(200,create(seed,kind))
  except QlooError as e:self.send(e.status,{"live":False,"error":e.message})
  except Exception:self.send(500,{"live":False,"error":"Unexpected processing problem."})
if __name__=="__main__":
 print("SIAS Taste-to-Science listening on 127.0.0.1:"+str(PORT)+", key "+("configured" if KEY else "pending"),flush=True)
 ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
