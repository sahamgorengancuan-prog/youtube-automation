import os
import sys
import types
import unittest
from unittest.mock import patch
from verify_model_access import check

class FakeModels:
    def list(self):
        return types.SimpleNamespace(data=[types.SimpleNamespace(id="muse-spark-1.3")])

class FakeClient:
    def __init__(self, **kwargs):
        assert kwargs.get("base_url") == "https://api.meta.ai/v1"
        self.models=FakeModels()

class FailedModels:
    def list(self):
        raise RuntimeError("simulated offline failure")

class FailedClient(FakeClient):
    def __init__(self, **kwargs):
        self.models=FailedModels()

class AccessProbeTests(unittest.TestCase):
    def test_without_key_is_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            result=check()
        self.assertEqual(result["status"],"NO_KEY_IN_PROCESS_ENVIRONMENT")
        self.assertEqual(result["generation_calls"],0)
    def test_mocked_success_without_network(self):
        fake=types.SimpleNamespace(OpenAI=FakeClient)
        with patch.dict(sys.modules, {"openai":fake}), patch.dict(os.environ, {"MODEL_API_KEY":"DUMMY_UNIT_TEST_ONLY"}):
            result=check()
        self.assertEqual(result["status"],"AUTHENTICATED")
        self.assertTrue(result["muse_spark_1_3_available"])
        self.assertEqual(result["generation_calls"],0)
    def test_fail_does_not_disclose_credentials(self):
        fake=types.SimpleNamespace(OpenAI=FailedClient)
        secret="DUMMY_VALUE_MUST_NOT_LEAK"
        with patch.dict(sys.modules, {"openai":fake}), patch.dict(os.environ, {"MODEL_API_KEY":secret}):
            result=check()
        self.assertEqual(result["status"],"VERIFICATION_FAILED")
        self.assertNotIn(secret,str(result))
        self.assertEqual(result["error_type"],"RuntimeError")

if __name__=="__main__":
    unittest.main()
