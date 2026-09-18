"""Optional OpenTelemetry setup.

Exporting is opt-in: with no OTLP endpoint and no Langfuse keys the SDK still
builds spans in-process (so the trace tree is complete) but ships nothing.
Langfuse is reached through its OTLP endpoint, so no extra SDK is needed.
"""

from __future__ import annotations

import base64
from typing import Any

from common.config import Settings
from common.telemetry.logging import get_logger

logger = get_logger(__name__)

_INSTALLED: dict[str, Any] = {}
_HTTPX_INSTRUMENTED = False


def install_otel(settings: Settings, service_id: str, module_id: str) -> Any | None:
    """Configure a tracer provider and return a tracer, or ``None`` if disabled."""
    if not settings.otel_enabled:
        logger.info("otel.disabled")
        return None

    cached = _INSTALLED.get(service_id)
    if cached is not None:
        return cached

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("otel.sdk_missing", extra={"hint": "pip install opentelemetry-sdk"})
        return None

    # One process normally hosts one service, but tests (and any all-in-one
    # host) load several. The global provider can only be set once, so later
    # services reuse it and are told apart by their span attributes instead.
    if _INSTALLED:
        tracer = trace.get_tracer(service_id, settings.application_version)
        _INSTALLED[service_id] = tracer
        return tracer

    resource = Resource.create(
        {
            "service.name": service_id,
            "service.version": settings.application_version,
            "service.namespace": settings.application_id,
            "deployment.environment": settings.environment,
            "sut.module_id": module_id,
            "sut.deployment_id": settings.deployment_id,
        }
    )
    provider = TracerProvider(resource=resource)

    endpoint, headers = _resolve_exporter(settings)
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
            )
            logger.info("otel.exporter_configured", extra={"endpoint": endpoint})
        except ImportError:  # pragma: no cover - optional dependency
            logger.warning("otel.otlp_exporter_missing")
        except Exception:  # pragma: no cover - never fail startup on telemetry
            logger.warning("otel.exporter_failed", exc_info=True)

    if settings.otel_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(service_id, settings.application_version)
    _INSTALLED[service_id] = tracer
    return tracer


def _resolve_exporter(settings: Settings) -> tuple[str | None, dict[str, str]]:
    """Prefer an explicit OTLP endpoint; otherwise fall back to Langfuse."""
    headers: dict[str, str] = {}

    if settings.otel_exporter_otlp_endpoint:
        if settings.otel_exporter_otlp_headers:
            headers = _parse_headers(settings.otel_exporter_otlp_headers)
        return settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces", headers

    if settings.langfuse_configured:
        token = base64.b64encode(
            f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"
        return settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces", headers

    return None, headers


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse the ``key=value,key2=value2`` form used by OTEL_EXPORTER_OTLP_HEADERS."""
    headers: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            headers[key.strip()] = value.strip()
    return headers


def instrument_app(app: Any) -> None:
    """Best-effort FastAPI + HTTPX auto-instrumentation."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metadata")
    except Exception:  # pragma: no cover - optional
        logger.debug("otel.fastapi_instrumentation_skipped", exc_info=True)

    global _HTTPX_INSTRUMENTED
    if not _HTTPX_INSTRUMENTED:
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            _HTTPX_INSTRUMENTED = True
        except Exception:  # pragma: no cover - optional
            logger.debug("otel.httpx_instrumentation_skipped", exc_info=True)


def reset_otel_cache() -> None:
    _INSTALLED.clear()
