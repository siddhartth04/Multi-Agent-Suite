"""Provider-neutral LLM client built on litellm.

Any provider litellm supports works by setting ``MODEL`` plus a key, e.g.::

    MODEL=gpt-4o-mini              LLM_API_KEY=sk-...
    MODEL=anthropic/claude-...     LLM_API_KEY=sk-ant-...
    MODEL=groq/llama-3.3-70b       LLM_API_KEY=gsk-...
    MODEL=ollama/llama3            LLM_API_BASE=http://127.0.0.1:11434

Token usage is read from the provider response when present; when it is not, a
local estimate is returned and explicitly labelled as such. Counts are never
fabricated.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from common.config import Settings
from common.telemetry import TokenSource, TokenUsage, get_logger

logger = get_logger(__name__)


class LLMError(RuntimeError):
    """Raised when an LLM call fails after exhausting retries."""


class LLMTimeout(TimeoutError):
    """Raised when an LLM call exceeds its configured timeout."""


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens: TokenUsage
    retry_count: int = 0
    finish_reason: str | None = None


class LLMClient:
    """Thin async wrapper over ``litellm.acompletion`` with retries and usage capture."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def model(self) -> str:
        return self.settings.model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        import litellm

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "timeout": self.settings.llm_timeout_seconds,
        }
        if self.settings.llm_api_key:
            kwargs["api_key"] = self.settings.llm_api_key
        if self.settings.llm_api_base:
            kwargs["api_base"] = self.settings.llm_api_base

        attempts = max(1, self.settings.llm_max_retries + 1)
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=self.settings.llm_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("llm.timeout", extra={"attempt": attempt + 1, "model": self.model})
                if attempt == attempts - 1:
                    raise LLMTimeout(
                        f"LLM call timed out after {self.settings.llm_timeout_seconds}s"
                    ) from exc
            except Exception as exc:  # noqa: BLE001 - provider errors vary widely
                last_error = exc
                logger.warning(
                    "llm.error",
                    extra={"attempt": attempt + 1, "model": self.model, "error": str(exc)[:200]},
                )
                if attempt == attempts - 1:
                    raise LLMError(f"LLM call failed: {exc}") from exc
            else:
                text = _extract_text(response)
                return LLMResponse(
                    text=text,
                    model=self.settings.model,
                    tokens=extract_usage(response, messages, text, self.settings.model),
                    retry_count=attempt,
                    finish_reason=_extract_finish_reason(response),
                )

            await asyncio.sleep(0.4 * (2**attempt))

        raise LLMError(f"LLM call failed: {last_error}")


def _extract_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError):
        try:
            content = response["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            return ""
    return (content or "").strip()


def _extract_finish_reason(response: Any) -> str | None:
    try:
        return response.choices[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return None


def extract_usage(response: Any, messages: list[dict[str, str]], text: str, model: str) -> TokenUsage:
    """Read provider usage; fall back to a labelled local estimate."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")

    if usage is not None:
        prompt = _usage_field(usage, "prompt_tokens")
        completion = _usage_field(usage, "completion_tokens")
        total = _usage_field(usage, "total_tokens")
        if prompt or completion or total:
            return TokenUsage(
                input_tokens=prompt or 0,
                output_tokens=completion or 0,
                total_tokens=total or ((prompt or 0) + (completion or 0)),
                cached_input_tokens=_cached_tokens(usage),
                reasoning_tokens=_reasoning_tokens(usage),
                cost_usd=_cost(response),
                source=TokenSource.PROVIDER,
            )

    return estimate_usage(messages, text, model)


def estimate_usage(messages: list[dict[str, str]], text: str, model: str) -> TokenUsage:
    """Approximate token counts, flagged ``ESTIMATED`` so consumers can tell."""
    try:
        import litellm

        prompt = int(litellm.token_counter(model=model, messages=messages))
        completion = int(litellm.token_counter(model=model, text=text)) if text else 0
    except Exception:  # noqa: BLE001 - tokenizer may not know a custom model
        prompt = sum(len(m.get("content", "")) for m in messages) // 4
        completion = len(text) // 4

    return TokenUsage(
        input_tokens=prompt,
        output_tokens=completion,
        total_tokens=prompt + completion,
        source=TokenSource.ESTIMATED,
    )


def _usage_field(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    return int(value) if isinstance(value, (int, float)) else None


def _cached_tokens(usage: Any) -> int | None:
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None and isinstance(usage, dict):
        details = usage.get("prompt_tokens_details")
    if details is None:
        return None
    value = getattr(details, "cached_tokens", None)
    if value is None and isinstance(details, dict):
        value = details.get("cached_tokens")
    return int(value) if isinstance(value, (int, float)) else None


def _reasoning_tokens(usage: Any) -> int | None:
    details = getattr(usage, "completion_tokens_details", None)
    if details is None and isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
    if details is None:
        return None
    value = getattr(details, "reasoning_tokens", None)
    if value is None and isinstance(details, dict):
        value = details.get("reasoning_tokens")
    return int(value) if isinstance(value, (int, float)) else None


def _cost(response: Any) -> float | None:
    """Ask litellm for the call cost; ``None`` when the model's pricing is unknown."""
    try:
        import litellm

        cost = litellm.completion_cost(completion_response=response)
        return round(float(cost), 8) if cost else None
    except Exception:  # noqa: BLE001 - unknown model pricing is normal
        return None
