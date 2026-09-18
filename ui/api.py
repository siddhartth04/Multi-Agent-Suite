"""HTTP client for the UI.

The dashboard is a pure consumer of the same public API the external testing
platform uses -- it never imports module or agent code, so what it displays is
exactly what the platform can observe.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_GATEWAY = "http://127.0.0.1:8000"
MODULE_PORTS = {
    "research": 8001,
    "fact_checker": 8002,
    "marketing": 8003,
    "travel": 8004,
}


def default_module_urls() -> dict[str, str]:
    """Module URLs from the environment, falling back to local ports.

    Lets the same dashboard image run against localhost, Docker service names,
    or separately deployed hosts without a code change.
    """
    return {
        module_id: os.getenv(f"{module_id.upper()}_URL", f"http://127.0.0.1:{port}").rstrip("/")
        for module_id, port in MODULE_PORTS.items()
    }


@dataclass
class ApiResult:
    """A call outcome that never raises, so the UI can always render something."""

    ok: bool
    data: Any = None
    error: str | None = None
    status_code: int | None = None

    @property
    def failed(self) -> bool:
        return not self.ok


def _get(url: str, timeout: float = 10.0) -> ApiResult:
    try:
        response = httpx.get(url, timeout=timeout)
        if response.status_code >= 400:
            return ApiResult(False, error=f"HTTP {response.status_code}", status_code=response.status_code)
        return ApiResult(True, data=response.json(), status_code=response.status_code)
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI, never raised
        return ApiResult(False, error=f"{type(exc).__name__}: {exc}"[:200])


def _post(url: str, payload: dict, timeout: float) -> ApiResult:
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        body = response.json() if response.content else None
        # A module answers a handled failure with 500/504 and a full telemetry
        # document, which the UI still wants to display.
        return ApiResult(
            ok=response.status_code < 400,
            data=body,
            error=None if response.status_code < 400 else f"HTTP {response.status_code}",
            status_code=response.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        return ApiResult(False, error=f"{type(exc).__name__}: {exc}"[:300])


class SutClient:
    """Reads topology, health and telemetry, and runs modules."""

    def __init__(self, gateway_url: str = DEFAULT_GATEWAY, module_urls: dict[str, str] | None = None) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.module_urls = module_urls or default_module_urls()

    # ---------------------------------------------------------- discovery
    def topology(self) -> ApiResult:
        return _get(f"{self.gateway_url}/topology")

    def gateway_health(self) -> ApiResult:
        return _get(f"{self.gateway_url}/health", timeout=15.0)

    def discovery(self) -> ApiResult:
        return _get(f"{self.gateway_url}/discovery", timeout=15.0)

    def module_health(self, module_id: str) -> ApiResult:
        return _get(f"{self.module_urls[module_id]}/health", timeout=5.0)

    def module_metadata(self, module_id: str) -> ApiResult:
        return _get(f"{self.module_urls[module_id]}/metadata", timeout=5.0)

    # --------------------------------------------------------- execution
    def run_module(
        self,
        module_id: str,
        user_input: str,
        *,
        failure_mode: str | None = None,
        use_dependencies: bool = True,
        timeout: float = 300.0,
    ) -> ApiResult:
        payload: dict[str, Any] = {"input": user_input, "use_dependencies": use_dependencies}
        if failure_mode and failure_mode != "normal":
            payload["failure_mode"] = failure_mode
        return _post(f"{self.module_urls[module_id]}/run", payload, timeout)

    # ------------------------------------------------------- observability
    def traces(self, module_id: str, limit: int = 20) -> ApiResult:
        return _get(f"{self.module_urls[module_id]}/telemetry/traces?limit={limit}")

    def trace(self, module_id: str, trace_id: str) -> ApiResult:
        return _get(f"{self.module_urls[module_id]}/telemetry/traces/{trace_id}")

    def distributed_trace(self, trace_id: str) -> ApiResult:
        return _get(f"{self.gateway_url}/telemetry/traces/{trace_id}", timeout=15.0)

    def tokens(self, module_id: str) -> ApiResult:
        return _get(f"{self.module_urls[module_id]}/telemetry/tokens")

    def clear_traces(self, module_id: str) -> ApiResult:
        try:
            response = httpx.delete(f"{self.module_urls[module_id]}/telemetry/traces", timeout=5.0)
            return ApiResult(response.status_code < 400, data=response.json())
        except Exception as exc:  # noqa: BLE001
            return ApiResult(False, error=str(exc)[:200])

    # ------------------------------------------------------------ helpers
    def all_health(self) -> dict[str, ApiResult]:
        return {module_id: self.module_health(module_id) for module_id in self.module_urls}

    def any_reachable(self) -> bool:
        return any(result.ok for result in self.all_health().values())
