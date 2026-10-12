"""Nebius Token Factory client (OpenAI-compatible API) with an offline fallback.

Every caller treats a `None` reply as "use the deterministic rules instead", so Sauda runs end to end
with no key and no network. The model never sees PayPal credentials and never calls PayPal: it only
returns JSON that deterministic code validates.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Settings

_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply (tolerates code fences and reasoning tags)."""
    if not text:
        return None
    text = _THINK.sub("", text)
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidates = ([fence.group(1)] if fence else []) + [text]
    for cand in candidates:
        cand = cand.strip()
        try:
            val = json.loads(cand)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass
        start = cand.find("{")
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            val = json.loads(cand[start : i + 1])
                            if isinstance(val, dict):
                                return val
                        except json.JSONDecodeError:
                            pass
                        break
            start = cand.find("{", start + 1)
    return None


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    by_purpose: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 1),
            "by_purpose": self.by_purpose,
        }


class LLM:
    """Small async JSON-in/JSON-out wrapper. Subclass or monkeypatch `_complete` in tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.usage = Usage()
        self._client = None
        self._sem = asyncio.Semaphore(max(1, settings.llm_concurrency))
        self._no_json_mode: set[str] = set()
        if settings.has_llm:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=settings.nebius_api_key,
                base_url=settings.nebius_base_url,
                timeout=settings.llm_timeout_s,
                max_retries=1,
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    def model_for(self, role: str = "agent") -> str:
        s = self.settings
        return {"agent": s.llm_model, "writer": s.llm_writer_model, "fast": s.llm_fast_model}.get(role, s.llm_model)

    def label(self, fast: bool = False, role: Optional[str] = None) -> str:
        if not self.available:
            return "rules"
        return "nebius:" + self.model_for(role or ("fast" if fast else "agent"))

    async def json(
        self,
        system: str,
        user: str,
        *,
        purpose: str,
        fast: bool = False,
        role: Optional[str] = None,
        max_tokens: int = 700,
        temperature: float = 0.4,
    ) -> Optional[dict]:
        if not self.available:
            return None
        model = self.model_for(role or ("fast" if fast else "agent"))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(2):
            try:
                text = await self._complete(model, messages, max_tokens, temperature)
            except Exception:  # network, auth, rate limit, timeout: fall back to rules
                self.usage.failures += 1
                if attempt == 0 and model != self.settings.llm_fast_model:
                    model = self.settings.llm_fast_model  # one retry on the smaller model
                    continue
                return None
            data = extract_json(text)
            if data is not None:
                self.usage.by_purpose[purpose] = self.usage.by_purpose.get(purpose, 0) + 1
                return data
            messages = messages + [
                {"role": "assistant", "content": text[-2000:]},
                {"role": "user", "content": "Reply with only the JSON object."},
            ]
        self.usage.failures += 1
        return None

    async def _complete(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        kwargs: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        if model not in self._no_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        async with self._sem:
            t0 = time.perf_counter()
            try:
                resp = await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs), timeout=self.settings.llm_timeout_s + 5
                )
            except Exception as e:
                if "response_format" in kwargs and "response_format" in str(e).lower():
                    self._no_json_mode.add(model)
                    kwargs.pop("response_format")
                    resp = await asyncio.wait_for(
                        self._client.chat.completions.create(**kwargs), timeout=self.settings.llm_timeout_s + 5
                    )
                else:
                    raise
            self.usage.seconds += time.perf_counter() - t0
        self.usage.calls += 1
        if resp.usage:
            self.usage.prompt_tokens += resp.usage.prompt_tokens or 0
            self.usage.completion_tokens += resp.usage.completion_tokens or 0
        msg = resp.choices[0].message
        return msg.content or getattr(msg, "reasoning_content", None) or ""
