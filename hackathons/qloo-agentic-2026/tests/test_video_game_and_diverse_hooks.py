import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s
class ProductTests(unittest.TestCase):
 def test_video_game_entity_type(self):
  self.assertEqual(s.TYPES["video_game"],"urn:entity:videogame")
 def test_cultural_affinity_changes_science_treatment(self):
  requests=[]
  def fake(endpoint,params):
   requests.append((endpoint,params))
   if endpoint=="/search":return {"results":[{"name":"Interstellar","id":"movie-123"}]}
   domain=params["filter.type"]
   d={
    "urn:entity:movie":"The Matrix",
    "urn:entity:artist":"Jazz Ensemble",
    "urn:entity:book":"Ocean Life"}
   return {"results":{"entities":[{"id":domain+"-a","name":d[domain]}]}}
  orig=s.request_qloo
  try:
   s.request_qloo=fake
   report=s.create("Interstellar","movie")
  finally:s.request_qloo=orig
  bycat={v["category"]:v for v in report["stories"]}
  self.assertEqual(bycat["movie"]["theme"],"Vision & perception")
  self.assertEqual(bycat["artist"]["theme"],"The physics of sound")
  self.assertEqual(bycat["book"]["theme"],"Ocean science")
  self.assertEqual(len({v["hook"] for v in report["stories"]}),3)
 def test_results_are_not_fake_when_key_is_missing(self):
  if not s.KEY:
   with self.assertRaises(s.QlooError):s.create("Minecraft","video_game")
