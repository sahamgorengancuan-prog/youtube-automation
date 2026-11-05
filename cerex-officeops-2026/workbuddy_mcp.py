#!/usr/bin/env python3
"""Local, fixture-only MCP bridge that Tencent WorkBuddy may connect to.
No network listener, no credentials, no external writes, no production access.
Protocol: JSON-RPC newline-delimited stdio. Verified with synthetic MCP handshake test.
"""
import json,sys
from pathlib import Path
from reconcile import ROOT, validate,reconcile,audit

TOOL_NAME="cerex_officeops_reconcile_fixture"
DEFINITION={
 "name":TOOL_NAME,
 "title":"CereX OfficeOps — Reconcile Demo Purchase Orders",
 "description":"Reconcile only built-in synthetic PO and invoice CSV files, and return a verifiable exception summary requiring human approval. Does not modify finance systems.",
 "inputSchema":{
  "type":"object","properties":{"dataset":{"type":"string","enum":["synthetic"],"description":"Only sponsor-safe synthetic fixture is available"}},
  "required":["dataset"],"additionalProperties":False
 }
}
def handler(request):
    if not isinstance(request,dict) or request.get("jsonrpc")!="2.0":
        return {"jsonrpc":"2.0","id":request.get("id") if isinstance(request,dict) else None,
                "error":{"code":-32600,"message":"Invalid Request"}}
    method=request.get("method","")
    reqid=request.get("id")
    if reqid is None:return None
    if method=="initialize":
        return {"jsonrpc":"2.0","id":reqid,"result":{"protocolVersion":"2025-06-18",
          "capabilities":{"tools":{"listChanged":False}},
          "serverInfo":{"name":"cerex-officeops-local-fixture","version":"0.1.0"}}}
    if method=="ping":return {"jsonrpc":"2.0","id":reqid,"result":{}}
    if method=="tools/list":return {"jsonrpc":"2.0","id":reqid,"result":{"tools":[DEFINITION]}}
    if method=="tools/call":
        args=request.get("params",{})
        if not isinstance(args,dict) or args.get("name")!=TOOL_NAME:
            return {"jsonrpc":"2.0","id":reqid,"error":{"code":-32602,"message":"Unsupported tool"}}
        params=args.get("arguments",{})
        if params!={"dataset":"synthetic"}:
            return {"jsonrpc":"2.0","id":reqid,"result":{"isError":True,
                "content":[{"type":"text","text":"Only the built-in synthetic dataset is allowed. No live customer records or external system access."}]}}
        try:
            path=ROOT/"mcp-output"
            path.mkdir(exist_ok=True)
            validate(path);summary=reconcile(path);attestation=audit(path)
            data={"result":"REVIEW_REQUIRED","summary":summary,
                  "audit":attestation,
                  "report":"mcp-output/findings.csv",
                  "evidence":"mcp-output/audit.json",
                  "real_workbuddy_session_verified":False,
                  "external_system_changes":0}
            return {"jsonrpc":"2.0","id":reqid,"result":{"content":[{"type":"text","text":json.dumps(data)}],"isError":False}}
        except (OSError,ValueError) as e:
            return {"jsonrpc":"2.0","id":reqid,"result":{"content":[{"type":"text","text":"UAT failed: "+type(e).__name__}],"isError":True}}
    return {"jsonrpc":"2.0","id":reqid,"error":{"code":-32601,"message":"Method not found"}}
def main():
    for line in sys.stdin:
        try:
            message=json.loads(line)
            answer=handler(message)
            if answer is not None:
                sys.stdout.write(json.dumps(answer,separators=(",",":"))+"\n")
                sys.stdout.flush()
        except (ValueError,TypeError):
            continue
if __name__=="__main__":main()
