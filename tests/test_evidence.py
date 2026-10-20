import json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import evidence_crew as crew

class TestCrew(unittest.TestCase):
 def setUp(self):
  self.case=crew.read_case(Path(__file__).resolve().parents[1]/"sample_case.json")
 def test_sources_and_prompt(self):
  p=crew.messages(self.case)
  self.assertEqual(len(p),2)
  self.assertIn("source",p[1]["content"])
 def test_bad_verdict_rejected(self):
  with self.assertRaises(ValueError):
   crew.validate({"verdict":"I invented this","confidence":0.5,"cited_source_ids":[1],"explanation":"x","suggested_revision":"y"},2)
 def test_bad_source_id_rejected(self):
  with self.assertRaises(ValueError):
   crew.validate({"verdict":"supported","confidence":0.5,"cited_source_ids":[100],"explanation":"x","suggested_revision":"y"},2)
 def test_missing_key_not_success(self):
  with patch.dict(os.environ,{"NEBIUS_API_KEY":"","NEBIUS_TOKEN_FACTORY_KEY":""}):
   with self.assertRaisesRegex(RuntimeError,"missing"):
    crew.evaluate(self.case)
 def test_response_parsing_and_transport_mock(self):
  response={"id":"UNIT_TEST_NOT_A_REAL_INFERENCE","choices":[{"message":{"content":json.dumps({
     "verdict":"supported","confidence":0.67,"cited_source_ids":[1],
     "explanation":"The excerpt supports variable clock rates.","suggested_revision":"Clarify comparison frame."})}}],
    "usage":{"prompt_tokens":30,"completion_tokens":40,"total_tokens":70}}
  with patch.object(crew,"call_nebius",return_value=response) as transport:
   r=crew.evaluate(self.case)
  transport.assert_called_once()
  self.assertEqual(r["verdict"]["verdict"],"supported")
  self.assertEqual(r["usage"]["total_tokens"],70)
  self.assertEqual(r["provider"],"Nebius Token Factory")
  self.assertEqual(r["response_id"],"UNIT_TEST_NOT_A_REAL_INFERENCE")
 def test_input_must_have_evidence(self):
  with self.assertRaises(ValueError):crew.validate({"verdict":"insufficient_evidence","confidence":0.8,"cited_source_ids":[-1],
      "explanation":"No sources","suggested_revision":"Do not claim."},2)
if __name__=="__main__":unittest.main()
