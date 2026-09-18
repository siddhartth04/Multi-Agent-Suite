"""Deterministic failure injection.

The SUT exists to be tested, so failures are configurable and repeatable --
never random. A mode may be set per service via ``FAILURE_MODE`` or per request
via the ``failure_mode`` body field, which lets one deployment exercise several
scenarios.
"""

from __future__ import annotations

import asyncio
from typing import Final

from common.config import Settings
from common.telemetry import get_logger

logger = get_logger(__name__)

SUPPORTED_MODES: Final[tuple[str, ...]] = (
    "normal",
    "slow",
    "error",
    "timeout",
    "tool_failure",
    "dependency_failure",
)


class InjectedFailure(RuntimeError):
    """Raised by the ``error`` mode: a deliberate, labelled application error."""


class InjectedTimeout(TimeoutError):
    """Raised by the ``timeout`` mode."""


class InjectedToolFailure(RuntimeError):
    """Raised by the ``tool_failure`` mode when a tool is invoked."""


class InjectedDependencyFailure(RuntimeError):
    """Raised by the ``dependency_failure`` mode on a cross-service call."""


def resolve_mode(requested: str | None, settings: Settings) -> str:
    """Per-request mode wins over the service default; unknown values fall back."""
    mode = (requested or settings.failure_mode or "normal").strip().lower()
    if mode not in SUPPORTED_MODES:
        logger.warning("failure.unknown_mode", extra={"requested": mode})
        return "normal"
    return mode


async def apply_entry_failure(mode: str, settings: Settings) -> None:
    """Run the failure behaviour that triggers as a request enters a module."""
    if mode == "normal":
        return

    logger.info("failure.injected", extra={"failure_mode": mode})

    if mode == "slow":
        await asyncio.sleep(settings.slow_mode_delay_seconds)
    elif mode == "error":
        raise InjectedFailure("Injected failure: FAILURE_MODE=error")
    elif mode == "timeout":
        # Sleep past the caller's timeout, then raise so the span is recorded as
        # a timeout even when the caller has already given up waiting.
        await asyncio.sleep(settings.timeout_mode_delay_seconds)
        raise InjectedTimeout("Injected timeout: FAILURE_MODE=timeout")


def check_tool_failure(mode: str) -> None:
    if mode == "tool_failure":
        raise InjectedToolFailure("Injected tool failure: FAILURE_MODE=tool_failure")


def check_dependency_failure(mode: str) -> None:
    if mode == "dependency_failure":
        raise InjectedDependencyFailure(
            "Injected dependency failure: FAILURE_MODE=dependency_failure"
        )
