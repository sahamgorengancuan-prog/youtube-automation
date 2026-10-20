"""Read-only-friendly web demo for the SIAS Nemotron Evidence Crew.

Live inference only after configuring NEBIUS_API_KEY, and protected by strict
DEMO_MAX_CALLS budget; by default the server listens on localhost only.
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock
import json,os,time
from evidence_crew import read_case,evaluate,NANO,SUPER
ROOT=Path(__file__).resolve().parent
LIMIT=max(0,min(100,int(os.getenv("DEMO_MAX_CALLS","8"))))
calls=0
mutex=Lock()
history=[]
def has_key():return bool(os.getenv("NEBIUS_API_KEY") or os.getenv("NEBIUS_TOKEN_FACTORY_KEY"))

class Handler(BaseHTTPRequestHandler):
    def send_json(self,status,obj):
        raw=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path=="/":
            b=(ROOT/"index.html").read_bytes()
            self.send_response(200);self.send_header("Content-Type","text/html;charset=utf-8")
            self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b);return
        if self.path=="/api/case":
            return self.send_json(200,read_case(ROOT/"sample_case.json"))
        if self.path=="/api/status":
            return self.send_json(200,{"project":"Nemotron Evidence Crew","provider":"Nebius Token Factory",
               "models":[NANO,SUPER],"ready":has_key(),"calls_left":max(0,LIMIT-calls),
               "recent_verified_calls":history[-10:]})
        return self.send_json(404,{"error":"Not found"})
    def do_POST(self):
        global calls
        if self.path!="/api/verify":return self.send_json(404,{"error":"Not found"})
        try:
            sz=int(self.headers.get("Content-Length","0"))
            if sz>8000:return self.send_json(413,{"error":"Request too large"})
            payload=json.loads(self.rfile.read(sz))
            variant=payload.get("variant","nano")
            if variant not in ("nano","super"):return self.send_json(400,{"error":"Invalid variant"})
            if not has_key():return self.send_json(503,{"error":"Nebius API key not configured; no model call has occurred."})
            with mutex:
                if calls>=LIMIT:return self.send_json(429,{"error":"Live inference limit reached."})
                calls+=1
            case=read_case(ROOT/"sample_case.json")
            result=evaluate(case,NANO if variant=="nano" else SUPER)
            history.append({"model":variant,"time":int(time.time()),"latency_ms":result["latency_ms"]})
            return self.send_json(200,result)
        except (TypeError,ValueError) as exc:return self.send_json(400,{"error":str(exc)[:160]})
        except Exception as exc:return self.send_json(502,{"error":str(exc)[:210]})
    def log_message(self,format,*args):print("web:",format%args,flush=True)

if __name__=="__main__":
    host=os.getenv("DEMO_HOST","127.0.0.1");port=int(os.getenv("DEMO_PORT","18978"))
    print("Demo on http://%s:%d — live calls budget %d"%(host,port,LIMIT),flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
