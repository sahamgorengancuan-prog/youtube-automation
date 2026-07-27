# Provider contracts

All adapters accept `transport(method, url, headers, body) -> (status, dict|bytes)`.
No adapter creates network traffic on its own; tests inject mocks; offline use
raises `ProviderRequestError`. **Consult current provider documentation before
live use — these shapes are the single place to update.**

## BFL (`providers/bfl.py`)
- submit: POST `{base}/{model}` body `{prompt, seed, width, height,
  output_format, safety_tolerance, prompt_upsampling, reference_images?}` →
  `{id}`
- poll: GET `{base}/get_result?id=` → `{status: Ready|Error|Request Moderated…,
  result:{sample}}`; moderation/timeouts are explicit failures
- download: bytes; < 4096 bytes → AssetIntegrityError

## OpenAI text (`providers/openai_text.py`)
- POST /chat/completions with `response_format: json_object`; content parsed as
  strict JSON (`ProviderSchemaError` otherwise)

## OpenAI audio (`providers/openai_audio.py`)
- TTS: POST /audio/speech `{model, voice, input, response_format}` → bytes
  (tiny responses rejected)
- Transcription: POST /audio/transcriptions (verbose_json + word/segment
  timestamps); a response without timestamps is a schema error

## OpenRouter (`providers/openrouter.py`)
- GET /models for the catalog (used by `model_resolver`)
- POST /chat/completions with image_url data URI; `provider.data_collection:
  "deny"` attached when configured; reviewer content must contain one JSON
  object

No credentials appear anywhere in code, logs, or notebooks.

## Higgsfield (OPTIONAL — sias_colab/providers/higgsfield.py)
- credentials: HF_KEY, or HF_API_KEY + HF_API_SECRET (never logged)
- GET /models · GET /models/{m}/cost · POST /generate (url or bytes; <4096B rejected)
- POST /analyze (Virality Predictor) — returns hook/attention/retention; `enabled: false` by default (may incur cost)
- `higgsfield model list|get`, `generate cost` CLI JSON parsed by parse_cli_model_list — schemas discovered live, never hard-coded
