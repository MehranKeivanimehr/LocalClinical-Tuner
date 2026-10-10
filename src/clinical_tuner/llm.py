"""Async chat clients behind one interface.

* ``provider: openai``    - any OpenAI-compatible server: vLLM, Ollama, llama.cpp server, SGLang (all local).
* ``provider: anthropic`` - Claude via the official Anthropic SDK. Used only to *generate synthetic data*
  (no patient data is ever sent); training, evaluation and serving stay 100% local.

Every client returns plain completion strings and keeps a running token-usage tally so generation cost can be
reported in the data card.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import openai
from tqdm.auto import tqdm

log = logging.getLogger(__name__)
T = TypeVar("T")
R = TypeVar("R")


class RefusalError(RuntimeError):
    """The model declined the request (Anthropic ``stop_reason == "refusal"``)."""


class ProviderUnavailableError(RuntimeError):
    """Credentials missing/invalid: the provider cannot be used at all (triggers backend fallback)."""


@dataclass
class EndpointConfig:
    model: str
    provider: str = "openai"  # openai | anthropic
    base_url: str | None = None
    label: str | None = None  # model identity used for provenance / "critic != generator" checks
    api_key_env: str | None = None
    max_concurrency: int = 32
    timeout: float = 600.0
    max_retries: int = 5
    extra_body: dict[str, Any] = field(default_factory=dict)
    # Anthropic-only knobs
    effort: str | None = None  # low | medium | high | xhigh | max; None = API default
    server_fallback: bool = True  # fallbacks="default": server-side retry of refused requests
    send_sampling_params: bool = False  # Claude 4.7+/5.x reject non-default temperature/top_p
    min_max_tokens: int = 16000  # adaptive thinking shares the max_tokens budget

    @property
    def identity(self) -> str:
        return self.label or self.model


class BaseChatClient:
    def __init__(self, cfg: EndpointConfig):
        self.cfg = cfg
        self._sem = asyncio.Semaphore(cfg.max_concurrency)
        self.usage: Counter[str] = Counter()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 1024,
        n: int = 1,
        seed: int | None = None,
        json_schema: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> list[str]:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - trivial
        pass


class OpenAICompatClient(BaseChatClient):
    def __init__(self, cfg: EndpointConfig):
        super().__init__(cfg)
        api_key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
        self._client = openai.AsyncOpenAI(base_url=cfg.base_url, api_key=api_key or "local",
                                          timeout=cfg.timeout, max_retries=0)
        self._structured_supported = True

    async def chat(self, messages, *, temperature=0.0, top_p=1.0, max_tokens=1024, n=1, seed=None,
                   json_schema=None, extra_body=None) -> list[str]:
        body = {**self.cfg.extra_body, **(extra_body or {})}
        kwargs: dict[str, Any] = dict(model=self.cfg.model, messages=messages, temperature=temperature,
                                      top_p=top_p, max_tokens=max_tokens, n=n, extra_body=body or None)
        if seed is not None:
            kwargs["seed"] = seed
        if json_schema is not None and self._structured_supported:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "output", "schema": json_schema, "strict": True}}
        async with self._sem:
            for attempt in range(self.cfg.max_retries + 1):
                try:
                    resp = await self._client.chat.completions.create(**kwargs)
                    if resp.usage:
                        self.usage["input_tokens"] += resp.usage.prompt_tokens or 0
                        self.usage["output_tokens"] += resp.usage.completion_tokens or 0
                    self.usage["requests"] += 1
                    return [(c.message.content or "") for c in resp.choices]
                except openai.BadRequestError as e:
                    if "response_format" in kwargs and "response_format" in str(e).lower():
                        log.warning("Endpoint rejected response_format; falling back to unconstrained JSON.")
                        self._structured_supported = False
                        kwargs.pop("response_format")
                        continue
                    raise
                except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                        openai.InternalServerError) as e:
                    if attempt == self.cfg.max_retries:
                        raise
                    delay = min(60.0, 2.0 ** attempt) + random.random()
                    log.warning("Transient error (%s); retry %d in %.1fs", type(e).__name__, attempt + 1, delay)
                    await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    async def close(self) -> None:
        await self._client.close()


class AnthropicClient(BaseChatClient):
    """Claude via the Anthropic SDK (credentials: ANTHROPIC_API_KEY or an `ant auth login` profile)."""

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, cfg: EndpointConfig):
        super().__init__(cfg)
        import anthropic  # local import: only needed when Claude is configured

        self._anthropic = anthropic
        kwargs: dict[str, Any] = dict(timeout=cfg.timeout, max_retries=cfg.max_retries)
        if cfg.api_key_env and os.environ.get(cfg.api_key_env):
            kwargs["api_key"] = os.environ[cfg.api_key_env]
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        try:
            self._client = anthropic.AsyncAnthropic(**kwargs)
        except Exception as e:  # missing credentials surface here in some SDK versions
            raise ProviderUnavailableError(f"Anthropic client could not be created: {e}") from e
        self._structured_supported = True

    async def _one(self, system: str | None, msgs: list[dict[str, str]], max_tokens: int,
                   temperature: float, top_p: float, json_schema: dict[str, Any] | None) -> str:
        a = self._anthropic
        params: dict[str, Any] = dict(model=self.cfg.model, max_tokens=max(max_tokens, self.cfg.min_max_tokens),
                                      messages=msgs)
        if system:
            # Stable instructions live in the system prompt so they can be prompt-cached across requests.
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        output_config: dict[str, Any] = {}
        if self.cfg.effort:
            output_config["effort"] = self.cfg.effort
        if json_schema is not None and self._structured_supported:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            params["output_config"] = output_config
        if self.cfg.send_sampling_params:
            params.update(temperature=temperature, top_p=top_p)

        try:
            if self.cfg.server_fallback:
                resp = await self._client.beta.messages.create(betas=[self.FALLBACK_BETA], fallbacks="default",
                                                               **params)
            else:
                resp = await self._client.messages.create(**params)
        except (a.AuthenticationError, a.PermissionDeniedError, a.CredentialsError) as e:
            raise ProviderUnavailableError(f"Anthropic credentials rejected: {e}") from e
        except a.BadRequestError as e:
            msg = str(e).lower()
            if "credential" in msg or "api_key" in msg or "authentication" in msg:
                raise ProviderUnavailableError(str(e)) from e
            if json_schema is not None and self._structured_supported and ("format" in msg or "schema" in msg):
                log.warning("Claude rejected the output schema (%s); falling back to prompt-only JSON.", e)
                self._structured_supported = False
                return await self._one(system, msgs, max_tokens, temperature, top_p, json_schema)
            raise
        except TypeError as e:  # e.g. SDK raises when no credential source can be resolved
            if "api_key" in str(e) or "auth" in str(e).lower():
                raise ProviderUnavailableError(str(e)) from e
            raise

        u = resp.usage
        self.usage["requests"] += 1
        self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        self.usage["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage["cache_creation_input_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            self.usage["refusals"] += 1
            raise RefusalError(f"refusal ({getattr(details, 'category', None)})")
        if resp.stop_reason == "max_tokens":
            self.usage["truncated"] += 1
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")

    async def chat(self, messages, *, temperature=0.0, top_p=1.0, max_tokens=1024, n=1, seed=None,
                   json_schema=None, extra_body=None) -> list[str]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system") or None
        msgs = [m for m in messages if m["role"] != "system"]
        async with self._sem:
            return [await self._one(system, msgs, max_tokens, temperature, top_p, json_schema) for _ in range(n)]

    async def close(self) -> None:
        await self._client.close()


def make_client(cfg: EndpointConfig) -> BaseChatClient:
    if cfg.provider == "anthropic":
        return AnthropicClient(cfg)
    if cfg.provider == "openai":
        return OpenAICompatClient(cfg)
    raise ValueError(f"Unknown provider {cfg.provider!r}")


# Backwards-compatible name used across the codebase for OpenAI-compatible endpoints.
ChatClient = OpenAICompatClient


async def gather_with_progress(
    items: Iterable[T], fn: Callable[[T], Awaitable[R]], desc: str
) -> list[R | BaseException]:
    """Run ``fn`` over items concurrently (concurrency is bounded inside the clients). Exceptions are returned,
    not raised, so one bad sample never kills a multi-hour generation run."""
    items = list(items)
    results: list[R | BaseException] = [None] * len(items)  # type: ignore[list-item]

    async def _run(i: int, item: T) -> None:
        try:
            results[i] = await fn(item)
        except Exception as e:  # noqa: BLE001 - surfaced to caller
            results[i] = e

    tasks = [asyncio.create_task(_run(i, it)) for i, it in enumerate(items)]
    for t in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=desc):
        await t
    return results


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any | None:
    """Leniently extract the first JSON object from model output (handles ``` fences, prose around JSON,
    and Qwen3-style <think> blocks). Returns None if nothing parseable is found."""
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)] + [text]
    for cand in candidates:
        cand = cand.strip()
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        start = cand.find("{")
        while start != -1:
            depth, in_str, esc = 0, False, False
            for j in range(start, len(cand)):
                ch = cand[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cand[start : j + 1])
                        except json.JSONDecodeError:
                            break
            start = cand.find("{", start + 1)
    return None
