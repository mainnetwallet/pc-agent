"""LLM Brain - provider-agnostic language model layer.

Supports OpenAI-compatible APIs, Anthropic native API, custom base URLs, and a
built-in rule-based fallback (mock) mode so the agent works without an API key.

The LLM is the reasoning engine only; actual computer operations always execute
through registered tools.
"""

import json
import os
import re
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
    def chat(self, messages: List[Dict[str, str]],
             tools: Optional[List[Dict]] = None,
             tool_choice: str = "auto",
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             stream: bool = False) -> LLMResponse:
        if self.provider == "mock" or not self.api_key:
            return self._fallback(messages, tools)
        try:
            if self.provider in ("openai", "custom"):
                return self._chat_openai(messages, tools, tool_choice,
                                         temperature, max_tokens)
            if self.provider == "anthropic":
                return self._chat_anthropic(messages, tools, temperature, max_tokens)
        except Exception as e:  # provider error (e.g. tools not supported)
            # Retry once without tools (plain text) before rule-based fallback
            try:
                if self.provider in ("openai", "custom"):
                    return self._chat_openai(messages, None, "none",
                                             temperature, max_tokens)
                if self.provider == "anthropic":
                    return self._chat_anthropic(messages, None, temperature, max_tokens)
            except Exception as e2:
                return LLMResponse(
                    text=f"[LLM provider error: {e2}]\n" + self._fallback_text(messages),
                    tool_calls=[])
        return self._fallback(messages, tools)

    # ------------------------------------------------------------ strict chat (reasoning loop)
    @property
    def available(self) -> bool:
        """True when a real provider is configured (API key set, provider != mock)."""
        return self.provider != "mock" and bool(self.api_key)

    def chat_strict(self, messages: List[Dict[str, str]],
                    images: Optional[List[str]] = None,
                    temperature: Optional[float] = None,
                    max_tokens: Optional[int] = None) -> LLMResponse:
        """Like chat(), but raises on provider errors instead of silently falling
        back to the rule-based planner. The reasoning loop needs real errors so it
        can retry, drop the screenshot, or fail the task visibly.

        images: optional screenshot file paths attached to the last user message.
        """
        if not self.available:
            raise RuntimeError("LLM not configured (set LLM_API_KEY and LLM_PROVIDER)")
        if self.provider in ("openai", "custom"):
            return self._chat_openai(messages, None, "none", temperature, max_tokens,
                                     images=images)
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, None, temperature, max_tokens,
                                        images=images)
        raise RuntimeError(f"Unsupported LLM provider: {self.provider}")

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

    # ------------------------------------------------------------ rule-based fallback
    def _fallback(self, messages, tools) -> LLMResponse:
        user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_text = m["content"]
                break
        return LLMResponse(text=self._fallback_text(messages), tool_calls=self._fallback_tool_calls(user_text, tools))

    def _fallback_text(self, messages) -> str:
        user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_text = m["content"]
                break
        provider_status = f"provider={self.provider}, model={self.model}"
        if not self.api_key:
            provider_status += " (no API key)"
        return (
            f"[FALLBACK planner - {provider_status}]\n"
            f"User instruction: {user_text}\n"
            "Reasoning: Using built-in rule-based intent resolution. "
            "Configure a working LLM provider for full autonomous reasoning."
        )

    def _fallback_tool_calls(self, user_text: str, tools) -> List[Dict]:
        """Heuristic intent resolution producing candidate tool calls."""
        calls: List[Dict] = []
        text = user_text.lower()
        # Tool name hints available
        available = []
        if tools:
            for t in tools:
                if isinstance(t, dict):
                    available.append(t.get("name", "") or (t.get("function", {}) or {}).get("name", ""))
                else:
                    available.append(getattr(t, "name", ""))
        available_set = set(available)

        # Notepad: open + type + save
        if "notepad" in text:
            if available_set and "desktop.launch_application" in available_set:
                pass  # handled by planner; here just note
        return calls

    # ------------------------------------------------------------ structured planning
    def plan(self, instruction: str, context: str = "",
             available_tools: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Ask the LLM (or fallback) to produce an execution plan as JSON."""
        # Build a list of available tool names for the LLM
        tool_names = []
        if available_tools:
            for t in available_tools:
                if isinstance(t, dict):
                    name = t.get("name", "")
                else:
                    name = getattr(t, "name", "")
                if name:
                    tool_names.append(name)
        tools_list = ", ".join(tool_names) if tool_names else "(none registered)"

        system = (
            "You are the planning engine of an autonomous Windows PC agent. "
            "Given a user instruction and context, produce a JSON execution plan "
            "with keys: interpreted_goal, assumptions, success_criteria, "
            "required_tools, execution_steps, dependencies, risk_level. "
            "execution_steps is a list of {step, tool, action, arguments}. "
            "CRITICAL: You MUST only use tool names from the Available Tools list below. "
            "Do not invent tool names. Available tools: " + tools_list + ". "
            "If no suitable tool exists, use 'desktop.screenshot' or 'desktop.launch_application' as appropriate. "
            "Be conservative and safe. Never include credentials."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Context: {context}\n\nInstruction: {instruction}\n\nAvailable tools: {available_tools or []}"},
        ]
        resp = self.chat(messages)
        # Try to parse JSON from response
        plan = self._extract_json(resp.text)
        if plan is None:
            plan = self._rule_based_plan(instruction, context, available_tools)
        return plan

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

    @staticmethod
    def _rule_based_plan(instruction, context, available_tools) -> Dict[str, Any]:
        text = instruction.lower()
        steps = []
        tools_needed = []

        def add_step(step, tool, action, args):
            steps.append({"step": step, "tool": tool, "action": action, "arguments": args})
            if tool and tool not in tools_needed:
                tools_needed.append(tool)

        if "notepad" in text:
            add_step(1, "desktop.launch_application", "open notepad", {"app_name_or_path": "Notepad"})
            if "type" in text or "write" in text:
                add_step(2, "desktop.type_text", "type text", {"text": instruction})
            if "save" in text:
                add_step(3, "desktop.hotkey", "press Ctrl+S to save", {"keys": ["ctrl", "s"]})
        elif any(w in text for w in ("open", "launch", "start")):
            target = text
            for w in ("open", "launch", "start", "the", "a", "an"):
                target = target.replace(w, " ")
            target = target.strip()
            add_step(1, "desktop.launch_application", f"open {target}", {"app_name_or_path": target})
        elif any(w in text for w in ("file", "folder", "directory", "document")):
            add_step(1, "filesystem.list_folder", "list folder", {"path": os.path.expanduser("~")})
        else:
            add_step(1, "desktop.screenshot", "capture current screen", {"monitor_index": 0})

        return {
            "interpreted_goal": instruction,
            "assumptions": ["Using rule-based fallback planner"],
            "success_criteria": ["Action executed without error"],
            "required_tools": tools_needed,
            "execution_steps": steps,
            "dependencies": [],
            "risk_level": "low",
        }


# Singleton instance
llm = LLM()
