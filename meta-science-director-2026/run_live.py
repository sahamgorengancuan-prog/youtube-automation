"""Optional live Meta call. Intentionally requires explicit cost acknowledgement.

No model calls occur by running tests, importing this file, or launching showcase_server.py.
"""
import argparse
import json
from pathlib import Path
from core import ScienceDirector
from meta_adapter import MetaMuseSpark

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--authorize-cost",action="store_true")
    p.add_argument("--output",default="live_result.local.json")
    args=p.parse_args()
    if not args.authorize_cost:
        p.error("Live API usage requires --authorize-cost after checking billing")
    fixture=json.loads((Path(__file__).resolve().parent/"fixtures"/"sky.json").read_text())
    result=ScienceDirector(MetaMuseSpark(),max_calls=3).run(fixture)
    output=Path(args.output).resolve()
    output.write_text(json.dumps(result,indent=2))
    output.chmod(0o600)
    print(json.dumps({"status":result["status"],"call_stages":result["calls"],
                      "output_path":str(output),"note":"Schema validity does not prove scientific correctness."}))
    return 0 if result["status"]!="BLOCK" else 1

if __name__=="__main__":
    raise SystemExit(main())
