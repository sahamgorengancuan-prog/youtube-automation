import json
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from showcase_server import Handler, preview, FIXTURE, HTML

class TestScienceDirectorShowcase(unittest.TestCase):
  def test_offline_model_calls_zero(self):
    out=preview()
    self.assertEqual(out["status"],"PREVIEW_ONLY")
    self.assertFalse(out["model_used"])
    self.assertEqual(out["meta_inference_calls"],0)
  def test_evidence_refs_are_whitelisted(self):
    out=preview()
    known={x["id"] for x in FIXTURE["source_excerpts"]}
    self.assertTrue(all(s["evidence_id"] in known for s in out["director"]["storyboard"]["scenes"]))
  def test_unknown_question_blocks(self):
    self.assertEqual(preview("How fast is an aircraft?")["status"],"BLOCK")
  def test_bad_input_blocks(self):
    self.assertEqual(preview("X"*260)["status"],"BLOCK")
  def test_html_contains_transparency(self):
    self.assertIn("not Meta inference",HTML)
    self.assertIn("local-only server",HTML)
  @classmethod
  def setUpClass(cls):
    cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
    cls.thread.start()
    cls.url=f"http://127.0.0.1:{cls.server.server_port}"
  @classmethod
  def tearDownClass(cls):
    cls.server.shutdown()
    cls.server.server_close()
  def test_health_rejects_paid_mode(self):
    x=json.load(urlopen(self.url+"/health",timeout=3))
    self.assertEqual(x["meta_inference_enabled"],False)
  def test_http_preview(self):
    req=Request(self.url+"/api/preview",data=json.dumps({"question":FIXTURE["question"]}).encode(),
                headers={"Content-Type":"application/json"},method="POST")
    x=json.load(urlopen(req,timeout=3))
    self.assertEqual(x["status"],"PREVIEW_ONLY")
  def test_http_unknown_question_rejected(self):
    req=Request(self.url+"/api/preview",data=b'{"question":"no citation"}',headers={"Content-Type":"application/json"},method="POST")
    with self.assertRaises(HTTPError) as ctx:urlopen(req,timeout=3)
    self.assertEqual(ctx.exception.code,422)

if __name__=="__main__":unittest.main()
