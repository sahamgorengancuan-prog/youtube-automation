from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from .config import StudioConfig
from .hardware import detect_hardware, release_gpu_memory, seed_everything
from .hashing import atomic_write_json, hash_value, read_json
from .logging_utils import configure_logging


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Local-first JSON LLM. Qwen (HF) is primary; OpenAI is an optional fallback.

    Every response is content-addressed and cached, so re-runs never re-generate
    identical prompts. The model is loaded lazily on first real call.
    """

    def __init__(self, config: StudioConfig, cache_dir: Path):
        self.config = config
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hardware = detect_hardware()
        self.logger = configure_logging("autostudio.llm")
        self._tokenizer = None
        self._model = None
        seed_everything(config.project.random_seed)

    @property
    def openai_available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip())

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Robust JSON recovery: strips fences, then brace-matches the first object."""
        cleaned = text.strip().replace("```json", "").replace("```", "")
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        for start in [m.start() for m in re.finditer(r"\{", cleaned)]:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(cleaned)):
                char = cleaned[index]
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == '"':
                    in_string = not in_string
                if in_string:
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:index + 1])
                        except json.JSONDecodeError:
                            break
        raise LLMError("Model output did not contain valid JSON.")

    def _load_local(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        model_id = self.config.llm.gpu_model if self.hardware.accelerator == "cuda" else self.config.llm.cpu_model
        self.logger.info("Loading %s on %s", model_id, self.hardware.device_name)
        self._tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
        kwargs: dict[str, Any] = {"low_cpu_mem_usage": True, "trust_remote_code": False}
        if self.hardware.accelerator == "cuda":
            dtype = torch.bfloat16 if self.hardware.supports_bf16 else torch.float16
            if self.config.llm.quantization == "4bit":
                kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=dtype,
                )
            kwargs["device_map"] = "auto"
            kwargs["dtype"] = dtype
        else:
            kwargs.update({"device_map": {"": "cpu"}, "dtype": torch.float32})
        try:
            self._model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
        except TypeError:
            # Older transformers use torch_dtype instead of dtype.
            dtype = kwargs.pop("dtype", None)
            if dtype is not None:
                kwargs["torch_dtype"] = dtype
            self._model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
        self._model.eval()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def _generate_local(self, system_prompt: str, user_prompt: str, max_new_tokens: int) -> dict[str, Any]:
        import torch

        self._load_local()
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        prompt = self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self._tokenizer(prompt, return_tensors="pt", truncation=True, max_length=self.config.llm.context_tokens)
        device = self._model.get_input_embeddings().weight.device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        deterministic = self.config.llm.temperature <= 0.2
        with torch.inference_mode():
            output = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=not deterministic,
                temperature=max(self.config.llm.temperature, 0.01),
                top_p=0.9,
                repetition_penalty=1.05,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = output[0, inputs["input_ids"].shape[1]:]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        try:
            return self._extract_json(text)
        except LLMError:
            # One deterministic repair pass to coerce malformed output into JSON.
            repair = f"Repair into one valid JSON object. Return JSON only:\n\n{text}"
            repair_messages = [{"role": "system", "content": "Repair malformed JSON."}, {"role": "user", "content": repair}]
            repair_text = self._tokenizer.apply_chat_template(repair_messages, tokenize=False, add_generation_prompt=True)
            repair_inputs = self._tokenizer(repair_text, return_tensors="pt", truncation=True, max_length=5000)
            repair_inputs = {key: value.to(device) for key, value in repair_inputs.items()}
            with torch.inference_mode():
                repaired = self._model.generate(
                    **repair_inputs, max_new_tokens=min(max_new_tokens, 1800),
                    do_sample=False, pad_token_id=self._tokenizer.eos_token_id,
                )
            return self._extract_json(self._tokenizer.decode(repaired[0, repair_inputs["input_ids"].shape[1]:], skip_special_tokens=True))

    def _generate_openai(self, system_prompt: str, user_prompt: str, max_new_tokens: int) -> dict[str, Any]:
        if not self.openai_available:
            raise LLMError("OPENAI_API_KEY is unavailable.")
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=self.config.llm.openai_model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=self.config.llm.temperature,
            max_tokens=max_new_tokens,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        cache_namespace: str,
        max_new_tokens: int | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        limit = max_new_tokens or self.config.llm.max_new_tokens
        key = hash_value({
            "model": self.config.llm.gpu_model, "system": system_prompt, "user": user_prompt,
            "limit": limit, "temperature": self.config.llm.temperature,
        })
        path = self.cache_dir / cache_namespace / f"{key}.json"
        if path.exists() and not force_refresh:
            cached = read_json(path)
            if isinstance(cached, dict):
                return cached
        local_error = None
        if self.config.llm.prefer_local:
            try:
                result = self._generate_local(system_prompt, user_prompt, limit)
                atomic_write_json(path, result)
                return result
            except Exception as exc:
                local_error = exc
                self.logger.warning("Local LLM failed: %s", exc)
                if "out of memory" in str(exc).lower():
                    release_gpu_memory()
        if self.config.llm.use_openai_fallback and self.openai_available:
            result = self._generate_openai(system_prompt, user_prompt, limit)
            atomic_write_json(path, result)
            return result
        raise LLMError(f"All LLM paths failed. Local error: {local_error}")

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        release_gpu_memory()
