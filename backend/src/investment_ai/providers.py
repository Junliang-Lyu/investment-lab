"""LLM providers behind one interface. Standard library HTTP only.

Default and only configured provider: Anthropic (DESIGN §17 #8). The Gemini
adapter is kept for later but is not used.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from pydantic import BaseModel

Post = Callable[[str, dict[str, str], bytes, float], bytes]

# USD per million tokens (input, output). Override with <PROVIDER>_PRICE_IN / _PRICE_OUT.
DEFAULT_PRICES = {
    "anthropic": (1.0, 5.0),   # small Claude model class
    # Paid-tier rate for gemini-3.8-flash through 2026-12-31 (doubles to 1.50 / 7.50
    # on 2027-01-01, per ai.google.dev/gemini-api/docs/pricing, checked 2026-09-27).
    # A Google project with billing enabled ("Tier 1 · Postpay" in AI Studio) is
    # charged per call. Override with GEMINI_PRICE_IN / GEMINI_PRICE_OUT.
    "gemini": (0.75, 3.75),
    "fake": (0.0, 0.0),
}
DEFAULT_MODELS = {"anthropic": "claude-haiku-4-5", "gemini": "gemini-3.8-flash", "fake": "fake-model"}


class LLMError(RuntimeError):
    pass


class LLMResult(BaseModel):
    provider: str
    model: str
    data: dict
    tokens_in: int
    tokens_out: int
    latency_ms: int
    truncated: bool = False
    text: str = ""  # text blocks outside the tool call (fields the model wrote there are recovered in validation)

    def cost_usd(self, prices: tuple[float, float]) -> float:
        return (self.tokens_in * prices[0] + self.tokens_out * prices[1]) / 1e6


class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int, strict: bool = False) -> LLMResult: ...


def _default_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode("utf-8", "replace")
        raise LLMError(f"HTTP {e.code} from {url.split('?')[0]}: {detail}") from None


# Per model (USD per million tokens, prompts up to 100K tokens; checked 2026-10-08 on the Anthropic pricing page).
# Longer prompts cost more (Haiku 5.5: 0.50 / 2.50), which the Lab never sends. A model not listed falls back to
# the provider default; <PROVIDER>_PRICE_IN / _PRICE_OUT still override everything.
MODEL_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}


def prices_for(provider: str, model: str | None = None) -> tuple[float, float]:
    base = MODEL_PRICES.get((model or "").lower()) or DEFAULT_PRICES.get(provider, (5.0, 25.0))  # unknown: assume expensive
    p = provider.upper()
    return (float(os.environ.get(f"{p}_PRICE_IN", base[0])), float(os.environ.get(f"{p}_PRICE_OUT", base[1])))


class AnthropicProvider:
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None, model: str | None = None, post: Post | None = None,
                 temperature: float = 0.2):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set (investment-lab/.env)")
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODELS["anthropic"]
        self._post, self.temperature = post or _default_post, temperature
        # Newer models refuse the temperature setting ("`temperature` is deprecated for this model", HTTP 400,
        # seen with claude-haiku-5-5 on 2026-10-08). They get none; an older name is retried once without it.
        self._send_temperature = not self.model.lower().startswith("claude-haiku-5")

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int, strict: bool = False) -> LLMResult:
        body = {
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "submit", "description": "Submit the structured result.", "input_schema": schema,
                       # Strict tool use (grammar-constrained output) is opt-in: with Haiku 4.5 it made 5 of 16
                       # eval calls run on to max_tokens (2026-09-28, v11 smoke). Code validates the schema anyway.
                       **({"strict": True} if strict and os.environ.get("ANTHROPIC_STRICT_TOOLS", "0") == "1" else {})}],
            "tool_choice": {"type": "tool", "name": "submit"},
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        t0 = time.monotonic()
        if self._send_temperature:
            body["temperature"] = self.temperature
        try:
            raw = self._post(self.URL, headers, json.dumps(body).encode(), 90)
        except LLMError as e:
            if not (self._send_temperature and "temperature" in str(e) and "deprecated" in str(e)):
                raise
            self._send_temperature = False
            body.pop("temperature", None)
            raw = self._post(self.URL, headers, json.dumps(body).encode(), 90)
        resp = json.loads(raw)
        latency = int((time.monotonic() - t0) * 1000)
        block = next((b for b in resp.get("content", []) if b.get("type") == "tool_use"), None)
        if block is None:
            raise LLMError(f"no structured output in response (stop_reason={resp.get('stop_reason')})")
        usage = resp.get("usage", {})
        text = "\n".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        return LLMResult(provider=self.name, model=resp.get("model", self.model), data=block["input"],
                         tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0),
                         latency_ms=latency, truncated=resp.get("stop_reason") == "max_tokens", text=text)


class GeminiProvider:
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str | None = None, model: str | None = None, post: Post | None = None,
                 temperature: float = 0.2):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set (investment-lab/.env)")
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS["gemini"]
        self._post, self.temperature = post or _default_post, temperature

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int, strict: bool = False) -> LLMResult:
        prompt = f"{user}\n\nReturn only a JSON object matching this JSON Schema:\n{json.dumps(schema)}"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"},
        }
        headers = {"x-goog-api-key": self.api_key, "content-type": "application/json"}
        t0 = time.monotonic()
        resp = json.loads(self._post(self.URL.format(model=self.model), headers, json.dumps(body).encode(), 90))
        latency = int((time.monotonic() - t0) * 1000)
        if resp.get("candidates", [{}])[0].get("finishReason") == "MAX_TOKENS":
            usage = resp.get("usageMetadata", {})
            return LLMResult(provider=self.name, model=self.model, data={}, truncated=True, latency_ms=latency,
                             tokens_in=usage.get("promptTokenCount", 0), tokens_out=usage.get("candidatesTokenCount", 0))
        try:
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"could not parse Gemini output: {e}") from None
        usage = resp.get("usageMetadata", {})
        return LLMResult(provider=self.name, model=self.model, data=data,
                         tokens_in=usage.get("promptTokenCount", 0), tokens_out=usage.get("candidatesTokenCount", 0),
                         latency_ms=latency)


class FakeProvider:
    """Returns queued responses; for tests and offline demos."""

    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list[dict]):
        self.responses, self.calls = list(responses), []

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int, strict: bool = False) -> LLMResult:
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        if "summaries" in schema.get("properties", {}):
            # The plain-language call (plain.py) is optional: unless a test queued summaries for it, it gets none
            # and the queue meant for the main calls stays untouched.
            if self.responses and isinstance(self.responses[0], dict) and "summaries" in self.responses[0]:
                data = self.responses.pop(0)
            else:
                data = {"summaries": []}
            return LLMResult(provider=self.name, model=self.model, data=data, tokens_in=len(user) // 4,
                             tokens_out=50, latency_ms=1)
        if not self.responses:
            raise LLMError("fake provider has no more responses")
        return LLMResult(provider=self.name, model=self.model, data=self.responses.pop(0),
                         tokens_in=len(user) // 4, tokens_out=200, latency_ms=1)


def make_provider(name: str) -> Provider:
    if name == "anthropic":
        return AnthropicProvider()
    if name == "gemini":
        return GeminiProvider()
    raise LLMError(f"unknown provider: {name}")
