"""Declarative agents and the sequential pipeline that runs them.

An :class:`AgentSpec` is data, so ``/metadata`` can describe the exact agents a
module will run without executing anything. :class:`AgentPipeline` executes them
in order, threading each agent's output into the next and opening one span per
agent and per LLM/tool call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence

from common.agents.llm import LLMClient, LLMError, LLMTimeout
from common.config import Settings
from common.telemetry import (
    AgentDescriptor,
    AgentExecution,
    Recorder,
    SpanKind,
    SpanStatus,
    TokenUsage,
    get_logger,
)

logger = get_logger(__name__)

ToolFn = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class AgentSpec:
    """Static definition of one agent."""

    agent_id: str
    role: str
    goal: str
    backstory: str
    instruction: str
    expected_output: str
    tools: tuple[str, ...] = ()
    max_tokens: int | None = None

    def descriptor(self, depends_on: list[str]) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self.agent_id,
            role=self.role,
            goal=self.goal,
            depends_on=depends_on,
            tools=list(self.tools),
        )

    def system_prompt(self) -> str:
        lines = [
            f"You are the {self.role}.",
            f"Goal: {self.goal}",
            f"Background: {self.backstory}",
            f"Produce exactly this deliverable: {self.expected_output}",
            "Be concise and concrete. Never claim an action you did not take.",
        ]
        if self.tools:
            # Tools are executed by the pipeline before the model is called, and
            # no tool schema is sent. Some models otherwise try to call a tool
            # themselves, which providers reject outright ("model called a tool"
            # while tool choice is none). Say plainly that the results are final.
            lines.append(
                "Any tool results below have already been gathered for you. "
                "You cannot call tools, and no tools are available to you. "
                "Work only with the information provided; if it is thin or empty, "
                "say so and continue using your own knowledge."
            )
        return "\n".join(lines)


@dataclass
class PipelineContext:
    """Everything an agent can see when it runs."""

    user_input: str
    external_context: str | None = None
    previous_outputs: list[tuple[str, str]] = field(default_factory=list)
    tool_results: dict[str, str] = field(default_factory=dict)

    def render(self, spec: AgentSpec) -> str:
        parts = [f"Request:\n{self.user_input}"]
        if self.external_context:
            parts.append(f"Context from an upstream module:\n{self.external_context}")
        for role, output in self.previous_outputs:
            parts.append(f"Output from the {role}:\n{output}")
        for tool_name, result in self.tool_results.items():
            parts.append(f"Result from tool `{tool_name}`:\n{result}")
        parts.append(f"Your task now:\n{spec.instruction}")
        return "\n\n---\n\n".join(parts)


class AgentPipeline:
    """Runs a fixed sequence of agents, recording telemetry for each step."""

    def __init__(
        self,
        specs: Sequence[AgentSpec],
        settings: Settings,
        recorder: Recorder,
        tools: dict[str, ToolFn] | None = None,
    ) -> None:
        self.specs = list(specs)
        self.settings = settings
        self.recorder = recorder
        self.tools = tools or {}
        self.llm = LLMClient(settings)

    @property
    def agent_ids(self) -> list[str]:
        return [s.agent_id for s in self.specs]

    def descriptors(self) -> list[AgentDescriptor]:
        """Describe the pipeline, wiring each agent's dependency to its predecessor."""
        out: list[AgentDescriptor] = []
        for index, spec in enumerate(self.specs):
            depends_on = [self.specs[index - 1].agent_id] if index else []
            out.append(spec.descriptor(depends_on))
        return out

    async def run(self, context: PipelineContext) -> tuple[list[AgentExecution], str]:
        """Execute every agent in order.

        Returns the per-agent records and the final agent's output. A failing
        agent aborts the pipeline; the exception carries the partial records so
        the caller can still report what ran.
        """
        executions: list[AgentExecution] = []

        for spec in self.specs:
            try:
                execution = await self._run_agent(spec, context)
            except Exception as exc:
                raise PipelineError(str(exc), executions) from exc

            executions.append(execution)
            if execution.output:
                context.previous_outputs.append((spec.role, execution.output))

        final = executions[-1].output if executions else ""
        return executions, final or ""

    async def _run_agent(self, spec: AgentSpec, context: PipelineContext) -> AgentExecution:
        with self.recorder.span(
            f"agent.{spec.agent_id}",
            SpanKind.AGENT,
            agent_id=spec.agent_id,
            attributes={"agent.role": spec.role, "agent.tools": list(spec.tools)},
        ) as agent_span:
            tokens = TokenUsage()

            for tool_name in spec.tools:
                tool_output = await self._run_tool(tool_name, context)
                if tool_output is not None:
                    context.tool_results[tool_name] = tool_output

            prompt = context.render(spec)
            response = await self._call_llm(spec, prompt)
            tokens = tokens.merge(response.tokens)

            agent_span.tokens = tokens
            agent_span.model = response.model
            agent_span.attributes["output_chars"] = len(response.text)

            return AgentExecution(
                agent_id=spec.agent_id,
                role=spec.role,
                status=SpanStatus.OK,
                duration_ms=agent_span.duration_ms or 0.0,
                tokens=tokens,
                model=response.model,
                output=response.text,
            )

    async def _call_llm(self, spec: AgentSpec, prompt: str):
        with self.recorder.span(
            f"llm.{spec.agent_id}",
            SpanKind.LLM,
            agent_id=spec.agent_id,
            attributes={"gen_ai.request.model": self.settings.model, "prompt_chars": len(prompt)},
        ) as llm_span:
            try:
                response = await self.llm.complete(
                    spec.system_prompt(), prompt, max_tokens=spec.max_tokens
                )
            except (LLMError, LLMTimeout):
                raise

            llm_span.tokens = response.tokens
            llm_span.model = response.model
            llm_span.retry_count = response.retry_count
            llm_span.attributes["finish_reason"] = response.finish_reason
            return response

    async def _run_tool(self, tool_name: str, context: PipelineContext) -> str | None:
        tool = self.tools.get(tool_name)
        if tool is None:
            logger.warning("tool.missing", extra={"tool": tool_name})
            return None

        with self.recorder.span(
            f"tool.{tool_name}", SpanKind.TOOL, attributes={"tool.name": tool_name}
        ) as tool_span:
            result = await tool(context.user_input)
            tool_span.attributes["result_chars"] = len(result)
            return result


class PipelineError(RuntimeError):
    """An agent failed. Carries the executions that completed beforehand."""

    def __init__(self, message: str, executions: list[AgentExecution]) -> None:
        super().__init__(message)
        self.executions = executions
