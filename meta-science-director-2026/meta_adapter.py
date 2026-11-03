"""Optional Meta Muse Spark client adapter.

Do not put API credentials in source, fixtures, logs, or the demo HTTP service.
This adapter is for explicitly authorized command-line model inference only.
"""
import json
import os

class MetaMuseSpark:
    def __init__(self):
        if not os.environ.get("MODEL_API_KEY"):
            raise RuntimeError("MODEL_API_KEY must be set in a trusted shell")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install optional dependency: pip install openai") from exc
        self._client = OpenAI(
            base_url="https://api.meta.ai/v1",
            api_key=os.environ["MODEL_API_KEY"],
            timeout=40.0,
            max_retries=0,
        )
        self.request_count = 0

    def complete(self, role: str, instruction: str, payload: dict) -> dict:
        if self.request_count >= 3:
            raise RuntimeError("Three-call ceiling reached")
        self.request_count += 1
        response=self._client.chat.completions.create(
            model="muse-spark-1.3",
            messages=[
                {"role":"developer", "content":instruction},
                {"role":"user", "content":json.dumps(payload,ensure_ascii=False)}
            ],
            max_completion_tokens=1200,
        )
        content=response.choices[0].message.content or ""
        cleaned=content.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned="\n".join(cleaned.splitlines()[1:-1]).strip()
        data=json.loads(cleaned)
        if not isinstance(data,dict):
            raise ValueError("Model response must be a JSON object")
        return data
