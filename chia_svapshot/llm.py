"""Provider-neutral text generation for the rewrite and expand nodes.

A model spec reads ``provider:model`` or just ``model``::

    vertex:gemini-2.5-pro        Gemini on Vertex AI        (GOOGLE_CLOUD_PROJECT + gcloud ADC)
    gemini:gemini-2.5-pro        Gemini API key             (GEMINI_API_KEY or GOOGLE_API_KEY)
    openai:gpt-5                 OpenAI or any server that  (OPENAI_API_KEY, OPENAI_BASE_URL)
                                 speaks /chat/completions   vLLM, Ollama, LiteLLM, llama.cpp
    nvidia:meta/llama-3.1-405b-instruct                     (NVIDIA_API_KEY)
    anthropic:claude-sonnet-4-5  Claude                     (ANTHROPIC_API_KEY)
    mock:replay                  canned reply from the file CHIA_LLM_MOCK_FILE (tests, dry runs)

A bare ``model`` name picks its provider from the name (see :func:`resolve`).
OpenAI, NVIDIA, Anthropic, and Gemini-API calls use HTTPS from the standard
library; only the Vertex provider needs the ``google-genai`` package.

``CHIA_LLM_MAX_TOKENS`` caps the reply length for every provider.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional

PROVIDERS = ("vertex", "gemini", "openai", "nvidia", "anthropic", "mock")

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    """A provider call failed or the provider is not configured."""


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def resolve(spec: str) -> ModelSpec:
    """Turn ``provider:model`` or ``model`` into a :class:`ModelSpec`."""
    spec = (spec or "").strip()
    if not spec:
        raise LLMError("empty model spec; set --llm or CHIA_LLM (for example vertex:gemini-2.5-pro)")
    head, sep, tail = spec.partition(":")
    if sep and head.lower() in PROVIDERS:
        if not tail:
            raise LLMError(f"model name missing in {spec!r}")
        return ModelSpec(head.lower(), tail)
    name = spec
    lower = name.lower()
    if lower.startswith("gemini"):
        has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        has_project = bool(os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT"))
        return ModelSpec("gemini" if has_key and not has_project else "vertex", name)
    if lower.startswith("claude"):
        return ModelSpec("anthropic", name)
    if lower.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")):
        return ModelSpec("openai", name)
    if "/" in name:
        return ModelSpec("nvidia" if os.environ.get("NVIDIA_API_KEY") else "openai", name)
    return ModelSpec("openai", name)


def bare_model(spec: str) -> str:
    """Model name without the provider prefix, as the SVApshot generator expects it."""
    return resolve(spec).model


def _max_tokens(requested: int) -> int:
    raw = os.environ.get("CHIA_LLM_MAX_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return min(requested, int(raw)) if requested else int(raw)
    return requested


def _require(name: str, *alternates: str) -> str:
    for key in (name, *alternates):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    names = " or ".join((name, *alternates))
    raise LLMError(f"set {names} to use this provider")


def _post_json(url: str, headers: Dict[str, str], body: dict, *, timeout: float = 900.0, retries: int = 3) -> dict:
    """POST JSON and return the decoded reply; retry on 429 and 5xx."""
    data = json.dumps(body).encode("utf-8")
    delay = 4.0
    last = ""
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:600]}"
            if exc.code not in (408, 409, 429, 500, 502, 503, 504) or attempt == retries:
                raise LLMError(last) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = str(exc)
            if attempt == retries:
                raise LLMError(last) from exc
        time.sleep(delay)
        delay *= 2
    raise LLMError(last)


_REASONING = ("o1", "o3", "o4", "gpt-5")


def _chat_completions(
    base_url: str, api_key: str, model: str, prompt: str, max_tokens: int, temperature: float,
    *, custom_server: bool,
) -> str:
    body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    reasoning = model.lower().startswith(_REASONING) and not custom_server
    if reasoning:
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
        body["temperature"] = temperature
    reply = _post_json(
        base_url.rstrip("/") + "/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        body,
    )
    try:
        return reply["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected chat/completions reply: {str(reply)[:400]}") from exc


def _openai(spec: ModelSpec, prompt: str, max_tokens: int, temperature: float) -> str:
    base = os.environ.get("OPENAI_BASE_URL", "").strip()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        if not base:
            raise LLMError("set OPENAI_API_KEY (and OPENAI_BASE_URL for a self-hosted server)")
        key = "EMPTY"
    return _chat_completions(
        base or OPENAI_BASE_URL, key, spec.model, prompt, max_tokens, temperature,
        custom_server=bool(base),
    )


def _nvidia(spec: ModelSpec, prompt: str, max_tokens: int, temperature: float) -> str:
    key = _require("NVIDIA_API_KEY")
    base = os.environ.get("NVIDIA_BASE_URL", "").strip() or NVIDIA_BASE_URL
    return _chat_completions(base, key, spec.model, prompt, max_tokens, temperature, custom_server=True)


def _anthropic(spec: ModelSpec, prompt: str, max_tokens: int, temperature: float) -> str:
    key = _require("ANTHROPIC_API_KEY")
    reply = _post_json(
        os.environ.get("ANTHROPIC_BASE_URL", "").strip() or ANTHROPIC_URL,
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        {
            "model": spec.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    parts = [block.get("text", "") for block in reply.get("content") or [] if block.get("type") == "text"]
    if not parts:
        raise LLMError(f"unexpected Anthropic reply: {str(reply)[:400]}")
    return "".join(parts)


def _gemini(spec: ModelSpec, prompt: str, max_tokens: int, temperature: float) -> str:
    key = _require("GEMINI_API_KEY", "GOOGLE_API_KEY")
    reply = _post_json(
        GEMINI_URL.format(model=spec.model),
        {"x-goog-api-key": key},
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        },
    )
    chunks = []
    for candidate in reply.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("text"):
                chunks.append(part["text"])
    if not chunks:
        raise LLMError(f"unexpected Gemini reply: {str(reply)[:400]}")
    return "".join(chunks)


_VERTEX_CLIENT = None


def _vertex(spec: ModelSpec, prompt: str, max_tokens: int, temperature: float) -> str:
    global _VERTEX_CLIENT
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if not project:
        raise LLMError("set GOOGLE_CLOUD_PROJECT (and run `gcloud auth application-default login`)")
    if _VERTEX_CLIENT is None:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMError("the Vertex provider needs `pip install google-genai`") from exc
        _VERTEX_CLIENT = genai.Client(
            vertexai=True, project=project,
            location=os.environ.get("GOOGLE_CLOUD_LOCATION") or "global",
        )
    response = _VERTEX_CLIENT.models.generate_content(
        model=spec.model,
        contents=prompt,
        config={
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "automatic_function_calling": {"disable": True},
        },
    )
    text = getattr(response, "text", None) or ""
    if text.strip():
        return text
    chunks = []
    for candidate in getattr(response, "candidates", None) or ():
        for part in getattr(getattr(candidate, "content", None), "parts", None) or ():
            if getattr(part, "text", None):
                chunks.append(part.text)
    return "".join(chunks)


def _mock(spec: ModelSpec, prompt: str, max_tokens: int, temperature: float) -> str:
    path = _require("CHIA_LLM_MOCK_FILE")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


_BACKENDS: Dict[str, Callable[[ModelSpec, str, int, float], str]] = {
    "vertex": _vertex,
    "gemini": _gemini,
    "openai": _openai,
    "nvidia": _nvidia,
    "anthropic": _anthropic,
    "mock": _mock,
}


def generate(
    spec: str,
    prompt: str,
    *,
    max_output_tokens: int = 16384,
    temperature: float = 0.2,
) -> str:
    """Send ``prompt`` to the model named by ``spec`` and return the reply text."""
    resolved = resolve(spec)
    return _BACKENDS[resolved.provider](
        resolved, prompt, _max_tokens(max_output_tokens), temperature,
    )


def default_spec() -> str:
    """The model spec from ``CHIA_LLM``, or an empty string."""
    return os.environ.get("CHIA_LLM", "").strip()
