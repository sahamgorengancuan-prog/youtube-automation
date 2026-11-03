"""Read-only Meta Model API catalogue check.

This command consumes MODEL_API_KEY from the trusted process environment.
It does not print the key, perform generation, or modify user billing.
"""
import json
import os
import sys

def check():
    if not os.getenv("MODEL_API_KEY"):
        return {"status":"NO_KEY_IN_PROCESS_ENVIRONMENT","authenticated":False,"generation_calls":0}
    try:
        from openai import OpenAI
        client=OpenAI(api_key=os.environ["MODEL_API_KEY"],base_url="https://api.meta.ai/v1",timeout=15,max_retries=0)
        response=client.models.list()
        models=[m.id for m in response.data if hasattr(m,"id")]
        return {"status":"AUTHENTICATED","authenticated":True,"generation_calls":0,
                "muse_spark_1_3_available":"muse-spark-1.3" in models,
                "models_returned":len(models)}
    except Exception as err:
        return {"status":"VERIFICATION_FAILED","authenticated":False,"generation_calls":0,
                "error_type":type(err).__name__}

if __name__=="__main__":
    result=check()
    print(json.dumps(result))
    sys.exit(0 if result["authenticated"] else 1)
