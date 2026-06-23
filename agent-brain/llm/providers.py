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

logger = logging.getLogger("valis.llm")


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""
    provider: str  # "openai", "anthropic", "ollama"
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
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
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return response.choices[0].message.content or ""

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
        return response.content[0].text

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
        resp = await self.http.post("/api/chat", json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
            },
        })
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]

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
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = "https://api.deepseek.com"
        if not config.model:
            config.model = "deepseek-chat"
        super().__init__(config)


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
