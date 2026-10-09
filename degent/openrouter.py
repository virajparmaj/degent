"""Explicit-model OpenRouter transport. Secrets never enter research artifacts."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .model_runner import ModelResponse

DEFAULT_MODEL = "openai/gpt-6-luna"
APPROVED_MODELS = (DEFAULT_MODEL, "openai/gpt-oss-20b", "anthropic/claude-haiku-5.5")
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def local_settings(path: Path | None = None) -> dict:
    """Read only supported literal dotenv fields; no shell expansion or logging."""
    result = {}
    path = path or Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name in {"OPENROUTER_API_KEY", "OPENROUTER_MODEL"}:
                result[name] = value.strip().strip("\"'")
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL"):
        if os.environ.get(name):
            result[name] = os.environ[name]
    return result


def model_identity(model: str = DEFAULT_MODEL) -> dict:
    if model not in APPROVED_MODELS:
        raise ValueError("Select one of the three explicitly approved OpenRouter models.")
    return {"provider": "openrouter", "model": model, "revision": "openrouter-chat-v1",
            "settings": {"structured_outputs": True, "allow_fallbacks": False, "require_parameters": True}}


def configuration(model: str = DEFAULT_MODEL) -> dict:
    return {"enabled": False, "identity": model_identity(model), "paid": True,
            "spending_authorized": False, "call_limit": 0, "input_token_limit": 0,
            "output_token_limit": 0, "max_output_tokens": 4096,
            "approved_models": list(APPROVED_MODELS)}


def strict_schema(schema: dict) -> dict:
    # OpenAI strict schemas require every property. Unstated optional fields use null.
    schema = copy.deepcopy(schema)
    item = schema["properties"]["r"]["items"]
    for name, value in item["properties"].items():
        if name not in item["required"]:
            item["properties"][name] = {"anyOf": [value, {"type": "null"}]}
    item["required"] = list(item["properties"])
    return schema


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward an Authorization header to another destination.


def payload_for(request: dict, model: str = DEFAULT_MODEL, settings: dict | None = None) -> dict:
    model_identity(model)
    provider = {"allow_fallbacks": False, "require_parameters": True}
    price = (settings or {}).get("pricing", {})
    if price:
        provider["max_price"] = {"prompt": price["input_per_million"], "completion": price["output_per_million"]}
    return {"model": model, "stream": False,
            "messages": [{"role": "system", "content": request["instructions"]},
                         {"role": "user", "content": json.dumps({"units": request["units"]}, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "degent_requirements", "strict": True, "schema": strict_schema(request["schema"])}},
            "max_tokens": request.get("max_output_tokens", 4096), "provider": provider}


class OpenRouterRunner:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, settings: dict | None = None):
        self.identity = model_identity(model)
        self._key = api_key if api_key is not None else local_settings().get("OPENROUTER_API_KEY", "")
        if not self._key.strip():
            raise ValueError("Add OPENROUTER_API_KEY to the workspace .env file.")
        self._settings = settings or {}

    def count_tokens(self, request: dict) -> None:
        return None  # Usage comes from the provider's native tokenizer.

    def reserve_input_tokens(self, request: dict) -> int:
        # Conservative byte-based allowance, not a tokenizer measurement.
        return len(json.dumps(payload_for(request, self.identity["model"], self._settings)).encode()) + 512

    def extract(self, request: dict) -> ModelResponse:
        body = payload_for(request, self.identity["model"], self._settings)
        http = Request(ENDPOINT, data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, method="POST")
        try:
            with build_opener(NoRedirect()).open(http, timeout=60) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise RuntimeError("OpenRouter response exceeded the transport limit.")
            data = json.loads(raw)
        except HTTPError as exc:
            raise RuntimeError(f"OpenRouter HTTP {exc.code}; no fallback was attempted.") from None
        except (URLError, OSError, ValueError):
            raise RuntimeError("OpenRouter transport or JSON response failed; no fallback was attempted.") from None
        if data.get("error"):
            raise RuntimeError("OpenRouter returned an API error; no fallback was attempted.")
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenRouter returned no completion.")
        usage = data.get("usage", {})
        cost = usage.get("cost")
        if cost is not None and (type(cost) not in {int, float} or not math.isfinite(cost) or cost < 0):
            raise RuntimeError("OpenRouter returned invalid billed usage.")
        # Redact the credential defensively even if an upstream diagnostic echoes it.
        output = choices[0].get("message", {}).get("content")
        if isinstance(output, str):
            output = output.replace(self._key, "[redacted]")
        return ModelResponse(output, {"input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"), "cost_usd": cost,
            "generation_id": data.get("id"), "provider": data.get("provider"),
            "reported_model": data.get("model"), "token_details": {
                "prompt": usage.get("prompt_tokens_details"), "completion": usage.get("completion_tokens_details")},
            "cost_details": usage.get("cost_details")}, choices[0].get("finish_reason", "unknown"))
