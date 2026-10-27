import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

class QlooContractTests(unittest.TestCase):
 def test_v2_insights_only_documented_parameters(self):
  observed=[]
  def mock(endpoint,params):
   observed.append((endpoint,params))
   if endpoint=="/search":return {"results":[{"name":"Interstellar","id":"qloo-id-1"}]}
   return {"results":{"entities":[{"name":"Cultural title","id":"id2"}]}}
  old=s.request_qloo;s.request_qloo=mock
  try:
   result=s.create("Interstellar","movie")
  finally:s.request_qloo=old
  self.assertTrue(result["stories"])
  self.assertEqual(observed[0][0],"/search")
  self.assertEqual(set(observed[0][1].keys()),{"query","types","take"})
  self.assertEqual([i[0] for i in observed[1:]],["/v2/insights"]*3)
  for endpoint,params in observed[1:]:
   self.assertEqual(set(params),{"filter.type","signal.interests.entities","take"})
   self.assertEqual(params["signal.interests.entities"],"qloo-id-1")
   self.assertEqual(params["take"],5)
 def test_unavailable_returns_honest_503(self):
  if not s.KEY:
   with self.assertRaises(s.QlooError) as ctx:s.create("Interstellar","movie")
   self.assertEqual(ctx.exception.status,503)
