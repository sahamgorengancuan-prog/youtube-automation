import json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
SERVER=str(ROOT/"workbuddy_mcp.py")

def send(requests):
    raw="\n".join(json.dumps({"jsonrpc":"2.0",**r}) for r in requests)+"\n"
    p=subprocess.run([sys.executable,SERVER],input=raw,text=True,capture_output=True,timeout=10,cwd=str(ROOT))
    if p.returncode!=0:raise RuntimeError("MCP stdio process failed")
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]

class WorkBuddyBridgeTest(unittest.TestCase):
    def test_handshake(self):
        r=send([{"id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}])
        self.assertEqual(r[0]["result"]["serverInfo"]["name"],"cerex-officeops-local-fixture")
    def test_tool_discovery(self):
        r=send([{"id":2,"method":"tools/list"}])
        self.assertEqual(r[0]["result"]["tools"][0]["name"],"cerex_officeops_reconcile_fixture")
    def test_reconcile(self):
        r=send([{"id":3,"method":"tools/call","params":{"name":"cerex_officeops_reconcile_fixture","arguments":{"dataset":"synthetic"}}}])
        self.assertFalse(r[0]["result"]["isError"])
        content=json.loads(r[0]["result"]["content"][0]["text"])
        self.assertEqual(content["audit"]["approval_status"],"NOT_APPROVED")
        self.assertEqual(content["summary"]["matched_rows"],2)
    def test_refuse_live_data(self):
        r=send([{"id":4,"method":"tools/call","params":{"name":"cerex_officeops_reconcile_fixture","arguments":{"dataset":"customer_exports"}}}])
        self.assertTrue(r[0]["result"]["isError"])
    def test_no_reply_to_notification(self):
        r=send([{"method":"notifications/initialized"}])
        self.assertEqual(r,[])
if __name__=="__main__":unittest.main()
