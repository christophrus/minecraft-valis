"""
Multi-provider LLM abstraction layer.
Supports OpenAI, Anthropic Claude, DeepSeek, and Ollama (local models).

Usage:
    from llm.providers import create_llm
    llm = create_llm("deepseek", model="deepseek-chat")
    response = await llm.chat([{"role": "user", "content": "Hello!"}])
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import logging
import os
import time

logger = logging.getLogger("valis.llm")

# --- Separate LLM call logger (writes to debug_logs/llm_calls.log) ---
_llm_call_logger = logging.getLogger("valis.llm.calls")
_llm_call_logger.propagate = False  # don't duplicate to main debug log
_llm_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug_logs")
os.makedirs(_llm_log_dir, exist_ok=True)
from datetime import datetime as _dt
_llm_log_path = os.path.join(_llm_log_dir, f"llm_calls-{_dt.now().strftime('%Y%m%d-%H%M%S')}.log")
_llm_fh = logging.FileHandler(_llm_log_path, encoding="utf-8")
_llm_fh.setFormatter(logging.Formatter("%(message)s"))
_llm_call_logger.addHandler(_llm_fh)
_llm_call_logger.setLevel(logging.INFO)

# Write CSV header if file is new/empty
if not os.path.exists(_llm_log_path) or os.path.getsize(_llm_log_path) == 0:
    _llm_call_logger.info("timestamp,provider,model,caller,prompt_tokens,completion_tokens,total_tokens,prompt_MTokens,completion_MTokens,total_MTokens,latency_s,finish_reason,response_preview")

# Session-level token accumulator
_session_totals = {"prompt": 0, "completion": 0, "calls": 0}


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""
    provider: str  # "openai", "anthropic", "ollama"
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    api_key: str = ""
    base_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]]) -> str:
        """Send a chat completion request and return the response text."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text."""
        ...


class OpenAIProvider(LLMProvider):
    """OpenAI API provider (GPT-4o, GPT-4, GPT-3.5, etc.)."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        import openai
        self.client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or None,
        )

    async def chat(self, messages: list[dict[str, str]]) -> str:
        model = self.config.model or "gpt-4o"
        t0 = time.time()
        # Determine caller context from call stack
        import traceback
        stack = traceback.extract_stack(limit=5)
        caller = "unknown"
        for frame in reversed(stack):
            if "providers.py" not in frame.filename and "agent" in frame.filename.lower():
                caller = f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
                break
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            latency = time.time() - t0
            choice = response.choices[0]
            content = choice.message.content or ""
            finish = choice.finish_reason or "unknown"

            # --- Token logging ---
            prompt_tok = response.usage.prompt_tokens if response.usage else 0
            compl_tok = response.usage.completion_tokens if response.usage else 0
            total_tok = prompt_tok + compl_tok
            _session_totals["prompt"] += prompt_tok
            _session_totals["completion"] += compl_tok
            _session_totals["calls"] += 1
            preview = content[:80].replace('"', "'").replace('\n', ' ').replace(',', ';')
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _llm_call_logger.info(
                f'{ts},{self.config.provider},{model},{caller},'
                f'{prompt_tok},{compl_tok},{total_tok},'
                f'{prompt_tok/1_000_000:.6f},{compl_tok/1_000_000:.6f},{total_tok/1_000_000:.6f},'
                f'{latency:.2f},{finish},"{preview}"'
            )
            logger.debug(
                f"LLM-CALL: {model} prompt={prompt_tok} compl={compl_tok} "
                f"total={total_tok} ({total_tok/1_000_000:.6f} MTokens) "
                f"latency={latency:.1f}s caller={caller} "
                f"session_total={_session_totals['prompt']+_session_totals['completion']} "
                f"({(_session_totals['prompt']+_session_totals['completion'])/1_000_000:.6f} MTokens in {_session_totals['calls']} calls)"
            )

            if not content:
                logger.warning(
                    f"LLM ({model}) empty content. "
                    f"finish_reason={finish} "
                    f"prompt_tokens={prompt_tok} "
                    f"completion_tokens={compl_tok}"
                )
                msg = choice.message
                logger.debug(f"LLM raw message: role={msg.role} content_type={type(msg.content).__name__} "
                           f"content_repr={repr(msg.content)[:200]} "
                           f"tool_calls={getattr(msg, 'tool_calls', None)} "
                           f"refusal={getattr(msg, 'refusal', None)}")
            return content
        except Exception as e:
            latency = time.time() - t0
            logger.error(f"LLM ({model}) API call FAILED after {latency:.1f}s: {type(e).__name__}: {e}")
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _llm_call_logger.info(
                f'{ts},{self.config.provider},{model},{caller},'
                f'0,0,0,0.000000,0.000000,0.000000,'
                f'{latency:.2f},ERROR,"{type(e).__name__}: {str(e)[:60]}"'
            )
            return ""

    async def embed(self, text: str) -> list[float]:
        model = self.config.extra.get("embedding_model", "text-embedding-3-small")
        response = await self.client.embeddings.create(
            model=model,
            input=text,
        )
        return response.data[0].embedding


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        import anthropic
        self.client = anthropic.AsyncAnthropic(api_key=config.api_key)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        model = self.config.model or "claude-sonnet-4-20250514"
        t0 = time.time()

        # Convert OpenAI-style messages to Anthropic format
        system_msg = ""
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                user_messages.append(msg)

        response = await self.client.messages.create(
            model=model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=system_msg if system_msg else None,
            messages=user_messages,
        )
        latency = time.time() - t0
        content = response.content[0].text
        prompt_tok = response.usage.input_tokens if response.usage else 0
        compl_tok = response.usage.output_tokens if response.usage else 0
        total_tok = prompt_tok + compl_tok
        _session_totals["prompt"] += prompt_tok
        _session_totals["completion"] += compl_tok
        _session_totals["calls"] += 1
        preview = content[:80].replace('"', "'").replace('\n', ' ').replace(',', ';')
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _llm_call_logger.info(
            f'{ts},anthropic,{model},anthropic_chat,'
            f'{prompt_tok},{compl_tok},{total_tok},'
            f'{prompt_tok/1_000_000:.6f},{compl_tok/1_000_000:.6f},{total_tok/1_000_000:.6f},'
            f'{latency:.2f},{response.stop_reason or "unknown"},"{preview}"'
        )
        logger.debug(
            f"LLM-CALL: {model} prompt={prompt_tok} compl={compl_tok} "
            f"total={total_tok} ({total_tok/1_000_000:.6f} MTokens) latency={latency:.1f}s"
        )
        return content

    async def embed(self, text: str) -> list[float]:
        # Anthropic doesn't have a public embeddings API.
        # Fall back to a simple hash-based embedding for now.
        # In production, use a separate embedding model.
        logger.warning("Anthropic does not provide embeddings. Using fallback.")
        return _fallback_embed(text)


class OllamaProvider(LLMProvider):
    """Ollama local model provider."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        import httpx
        self.http = httpx.AsyncClient(
            base_url=config.base_url or "http://localhost:11434",
            timeout=httpx.Timeout(120.0),
        )

    async def chat(self, messages: list[dict[str, str]]) -> str:
        model = self.config.model or "llama3"
        t0 = time.time()
        resp = await self.http.post("/api/chat", json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
            },
        })
        resp.raise_for_status()
        latency = time.time() - t0
        data = resp.json()
        content = data["message"]["content"]
        prompt_tok = data.get("prompt_eval_count", 0)
        compl_tok = data.get("eval_count", 0)
        total_tok = prompt_tok + compl_tok
        _session_totals["prompt"] += prompt_tok
        _session_totals["completion"] += compl_tok
        _session_totals["calls"] += 1
        preview = content[:80].replace('"', "'").replace('\n', ' ').replace(',', ';')
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _llm_call_logger.info(
            f'{ts},ollama,{model},ollama_chat,'
            f'{prompt_tok},{compl_tok},{total_tok},'
            f'{prompt_tok/1_000_000:.6f},{compl_tok/1_000_000:.6f},{total_tok/1_000_000:.6f},'
            f'{latency:.2f},done,"{preview}"'
        )
        logger.debug(
            f"LLM-CALL: {model} prompt={prompt_tok} compl={compl_tok} "
            f"total={total_tok} ({total_tok/1_000_000:.6f} MTokens) latency={latency:.1f}s"
        )
        return content

    async def embed(self, text: str) -> list[float]:
        model = self.config.model or "llama3"
        resp = await self.http.post("/api/embeddings", json={
            "model": model,
            "prompt": text,
        })
        resp.raise_for_status()
        data = resp.json()
        return data["embedding"]


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek API provider — OpenAI-compatible API.

    Models: deepseek-chat (V3), deepseek-reasoner (R1).
    Base URL: https://api.deepseek.com
    Note: DeepSeek does not offer embeddings. Uses fallback.
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = "https://api.deepseek.com"
        if not config.model:
            config.model = "deepseek-chat"
        super().__init__(config)

    async def embed(self, text: str) -> list[float]:
        """DeepSeek has no embeddings API. Use fallback hash-based embedding."""
        return _fallback_embed(text)


def _fallback_embed(text: str) -> list[float]:
    """Simple fallback embedding using character n-gram hashing."""
    import hashlib
    n = 3
    vec = [0.0] * 128
    for i in range(len(text) - n + 1):
        ngram = text[i:i + n]
        h = int(hashlib.md5(ngram.encode()).hexdigest(), 16) % 128
        vec[h] += 1.0
    # Normalize
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def get_session_token_totals() -> dict:
    """Return session-level token totals for external reporting."""
    total = _session_totals["prompt"] + _session_totals["completion"]
    return {
        "prompt_tokens": _session_totals["prompt"],
        "completion_tokens": _session_totals["completion"],
        "total_tokens": total,
        "total_MTokens": total / 1_000_000,
        "calls": _session_totals["calls"],
    }


def log_session_summary():
    """Write a summary line to the LLM call log. Call on shutdown."""
    t = get_session_token_totals()
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _llm_call_logger.info(
        f'{ts},--- SESSION SUMMARY ---,,'
        f'{t["calls"]} calls,'
        f'{t["prompt_tokens"]},{t["completion_tokens"]},{t["total_tokens"]},'
        f'{t["prompt_tokens"]/1_000_000:.6f},{t["completion_tokens"]/1_000_000:.6f},{t["total_MTokens"]:.6f},'
        f',,""'
    )
    logger.info(
        f"LLM SESSION SUMMARY: {t['calls']} calls, "
        f"{t['total_tokens']} tokens ({t['total_MTokens']:.6f} MTokens), "
        f"prompt={t['prompt_tokens']} completion={t['completion_tokens']}"
    )


def create_llm(provider: str, **kwargs) -> LLMProvider:
    """Factory function to create an LLM provider instance."""
    config = LLMConfig(provider=provider, **kwargs)

    # Load API keys from environment if not provided
    if not config.api_key:
        if provider == "openai":
            config.api_key = __import__("os").getenv("OPENAI_API_KEY", "")
        elif provider == "anthropic":
            config.api_key = __import__("os").getenv("ANTHROPIC_API_KEY", "")
        elif provider == "deepseek":
            config.api_key = __import__("os").getenv("DEEPSEEK_API_KEY", "")

    match provider:
        case "openai":
            return OpenAIProvider(config)
        case "deepseek":
            return DeepSeekProvider(config)
        case "anthropic":
            return AnthropicProvider(config)
        case "ollama":
            return OllamaProvider(config)
        case _:
            raise ValueError(f"Unknown LLM provider: {provider}")
