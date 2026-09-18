"""Reusable client for module-to-module calls.

Cross-service calls are the part the testing platform cares most about, so every
call: propagates W3C trace context, uses explicit connect/read timeouts, retries
idempotently with backoff, and records a ``service_call`` span whether it
succeeds, times out, or fails.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from common.config import Settings
from common.telemetry import (
    DependencyCall,
    Recorder,
    SpanKind,
    SpanStatus,
    TokenUsage,
    get_logger,
    get_trace_context,
)

logger = get_logger(__name__)


class DependencyError(RuntimeError):
    """A downstream module could not be reached or returned an error."""

    def __init__(self, message: str, call: DependencyCall) -> None:
        super().__init__(message)
        self.call = call


class DependencyClient:
    """Calls another module's ``/run`` endpoint over HTTP."""

    def __init__(self, settings: Settings, recorder: Recorder) -> None:
        self.settings = settings
        self.recorder = recorder

    async def run_module(
        self,
        module_id: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> tuple[dict[str, Any], DependencyCall]:
        """POST to ``{module}/run``, returning the body and the call record."""
        base_url = self.settings.service_url(module_id)
        url = f"{base_url}/run"
        ctx = get_trace_context().child()
        headers = ctx.to_headers()

        read_timeout = timeout or self.settings.dependency_timeout_seconds
        timeouts = httpx.Timeout(
            read_timeout,
            connect=self.settings.dependency_connect_timeout_seconds,
        )

        with self.recorder.span(
            f"service_call.{module_id}",
            SpanKind.SERVICE_CALL,
            attributes={
                "http.url": url,
                "http.method": "POST",
                "dependency.module_id": module_id,
                "dependency.timeout_s": read_timeout,
            },
        ) as span:
            attempts = max(1, self.settings.dependency_max_retries + 1)
            last_error: Exception | None = None
            # The span's duration is only computed when it closes, which happens
            # after these records are built, so time the call directly.
            call_start = time.perf_counter()

            def elapsed_ms() -> float:
                return round((time.perf_counter() - call_start) * 1000, 3)

            for attempt in range(attempts):
                span.retry_count = attempt
                try:
                    async with httpx.AsyncClient(timeout=timeouts) as client:
                        response = await client.post(url, json=payload, headers=headers)
                except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                    last_error = exc
                    logger.warning(
                        "dependency.timeout",
                        extra={"module_id": module_id, "attempt": attempt + 1, "url": url},
                    )
                    if attempt == attempts - 1:
                        call = DependencyCall(
                            module_id=module_id,
                            url=url,
                            status=SpanStatus.TIMEOUT,
                            duration_ms=elapsed_ms(),
                            retry_count=attempt,
                            error=f"timeout after {read_timeout}s",
                        )
                        span.attributes["dependency.status"] = "timeout"
                        raise DependencyError(f"{module_id} timed out", call) from exc
                except httpx.HTTPError as exc:
                    last_error = exc
                    logger.warning(
                        "dependency.transport_error",
                        extra={"module_id": module_id, "attempt": attempt + 1, "error": str(exc)[:200]},
                    )
                    if attempt == attempts - 1:
                        call = DependencyCall(
                            module_id=module_id,
                            url=url,
                            status=SpanStatus.ERROR,
                            duration_ms=elapsed_ms(),
                            retry_count=attempt,
                            error=f"{type(exc).__name__}: {exc}"[:300],
                        )
                        span.attributes["dependency.status"] = "error"
                        raise DependencyError(f"{module_id} unavailable: {exc}", call) from exc
                else:
                    span.attributes["http.status_code"] = response.status_code

                    if response.status_code >= 500 and attempt < attempts - 1:
                        last_error = httpx.HTTPStatusError(
                            f"server error {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        await asyncio.sleep(
                            self.settings.dependency_retry_backoff_seconds * (2**attempt)
                        )
                        continue

                    if response.status_code >= 400:
                        call = DependencyCall(
                            module_id=module_id,
                            url=url,
                            status=SpanStatus.ERROR,
                            duration_ms=elapsed_ms(),
                            retry_count=attempt,
                            http_status=response.status_code,
                            error=response.text[:300],
                        )
                        raise DependencyError(
                            f"{module_id} returned HTTP {response.status_code}", call
                        )

                    body = response.json()
                    tokens = _tokens_from_body(body)
                    if tokens is not None:
                        span.tokens = tokens

                    call = DependencyCall(
                        module_id=module_id,
                        url=url,
                        status=SpanStatus.OK,
                        duration_ms=elapsed_ms(),
                        retry_count=attempt,
                        http_status=response.status_code,
                        tokens=tokens,
                    )
                    return body, call

                await asyncio.sleep(self.settings.dependency_retry_backoff_seconds * (2**attempt))

            call = DependencyCall(
                module_id=module_id,
                url=url,
                status=SpanStatus.ERROR,
                duration_ms=elapsed_ms(),
                retry_count=attempts - 1,
                error=str(last_error)[:300] if last_error else "unknown error",
            )
            raise DependencyError(f"{module_id} failed after {attempts} attempts", call)


def _tokens_from_body(body: dict[str, Any]) -> TokenUsage | None:
    """Pull the downstream module's token usage so it rolls up into ours."""
    raw = body.get("tokens")
    if not isinstance(raw, dict):
        return None
    try:
        return TokenUsage.model_validate(raw)
    except Exception:  # noqa: BLE001 - a malformed body must not break the call
        return None


async def check_health(url: str, timeout: float = 3.0) -> dict[str, Any]:
    """Probe a service's ``/health``; never raises."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}
