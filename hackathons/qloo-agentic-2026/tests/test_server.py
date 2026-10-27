import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s
class Tests(unittest.TestCase):
 def test_nested_insights(self):self.assertEqual(s.unpack({"success":True,"results":{"entities":[{"name":"Alpha"}]}})[0]["name"],"Alpha")
 def test_search(self):self.assertEqual(s.unpack({"results":[{"id":"123"}]})[0]["id"],"123")
 def test_entity_id(self):self.assertEqual(s.eid({"id":"abc"}),"abc")
 def test_science(self):self.assertEqual(s.lens("jazz")[0],"The physics of sound")
 def test_pending(self):
  if not s.KEY:
   with self.assertRaises(s.QlooError):s.request_qloo("/search",{"query":"test"})
if __name__=="__main__":unittest.main()
