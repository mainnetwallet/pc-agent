"""LLM Brain - provider-agnostic language model layer.

Supports OpenAI-compatible APIs, the Anthropic native API and custom base URLs.
There is NO rule-based or mock fallback: planning, intent resolution and every
decision are made by the LLM. If no provider/API key is configured, or the provider
fails, calls raise an error so the task fails visibly instead of being guessed.

The LLM is the reasoning engine only; actual computer operations always execute
through registered tools.
"""

import json
from typing import Any, Dict, List, Optional

from config import (
    LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    LLM_TEMPERATURE, LLM_MAX_TOKENS, LLM_ENABLE_VISION,
)


class LLMResponse:
    def __init__(self, text: str, tool_calls: Optional[List[Dict]] = None,
                 raw: Optional[Any] = None):
        self.text = text
        self.tool_calls = tool_calls or []
        self.raw = raw


class LLM:
    def __init__(self):
        self.provider = LLM_PROVIDER
        self.api_key = LLM_API_KEY
        self.base_url = LLM_BASE_URL
        self.model = LLM_MODEL
        self.temperature = LLM_TEMPERATURE
        self.max_tokens = LLM_MAX_TOKENS
        self._http = None  # lazy httpx client

    # ------------------------------------------------------------------ chat
    SUPPORTED_PROVIDERS = ("openai", "custom", "anthropic")

    @property
    def available(self) -> bool:
        """True when a real provider is configured (supported provider + API key set)."""
        return self.provider in self.SUPPORTED_PROVIDERS and bool(self.api_key)

    def chat(self, messages: List[Dict[str, str]],
             images: Optional[List[str]] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> LLMResponse:
        """Send a conversation to the configured LLM and return its reply.

        Raises on any problem (not configured, provider error) - there is no silent
        fallback, so callers can retry, drop the screenshot, or fail the task visibly.

        images: optional screenshot file paths attached to the last user message.
        """
        if not self.available:
            raise RuntimeError(
                "LLM not configured: set LLM_API_KEY and LLM_PROVIDER "
                "(openai | anthropic | custom) in .env")
        if self.provider in ("openai", "custom"):
            return self._chat_openai(messages, None, "none", temperature, max_tokens,
                                     images=images)
        return self._chat_anthropic(messages, None, temperature, max_tokens, images=images)

    # Kept as an alias: the reasoning loop calls chat_strict().
    chat_strict = chat

    @staticmethod
    def _encode_image(path: str):
        """Return (base64, media_type) for a screenshot, downscaled to save tokens."""
        import base64
        import io
        try:
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((1280, 1280))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=70)
            return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"
        except Exception:
            try:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode(), "image/png"
            except Exception:
                return None

    def _attach_images(self, messages, images, style: str):
        """Return a copy of messages with screenshots added to the last user message."""
        if not images:
            return messages
        encoded = [e for e in (self._encode_image(p) for p in images) if e]
        if not encoded:
            return messages
        out = [dict(m) for m in messages]
        for i in range(len(out) - 1, -1, -1):
            if out[i]["role"] != "user":
                continue
            text = out[i]["content"]
            if style == "openai":
                parts = [{"type": "text", "text": text}] + [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mt};base64,{b64}"}}
                    for b64, mt in encoded]
            else:  # anthropic
                parts = [{"type": "image",
                          "source": {"type": "base64", "media_type": mt, "data": b64}}
                         for b64, mt in encoded] + [{"type": "text", "text": text}]
            out[i]["content"] = parts
            break
        return out

    # ------------------------------------------------------------ OpenAI-compatible
    def _chat_openai(self, messages, tools, tool_choice, temperature, max_tokens,
                     images=None):
        import httpx
        messages = self._attach_images(messages, images, "openai")
        base = self.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            tool_calls.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": tc["function"]["arguments"],
            })
        return LLMResponse(text=msg.get("content", "") or "", tool_calls=tool_calls, raw=data)

    # ------------------------------------------------------------ Anthropic
    def _chat_anthropic(self, messages, tools, temperature, max_tokens, images=None):
        import httpx
        messages = self._attach_images(messages, images, "anthropic")
        base = self.base_url or "https://api.anthropic.com/v1"
        url = base.rstrip("/") + "/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        system = ""
        convo = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                convo.append({"role": m["role"], "content": m["content"]})
        payload = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "system": system,
            "messages": convo,
        }
        if tools:
            payload["tools"] = tools
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        text = ""
        tool_calls = []
        for block in data.get("content", []):
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": block.get("input", {}),
                })
        return LLMResponse(text=text, tool_calls=tool_calls, raw=data)

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        # Find the first {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        return None


# Singleton instance
llm = LLM()
