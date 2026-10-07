"""miniagents: a ~1k-line reproduction of the openai-agents-python architecture.

It reproduces the architecture, not the product: a Runner loop over a NextStep state
machine, items as the only data format, handoffs and sub-agents as tools, errors as
observations, approvals as a serializable pause, sessions as an append-only log, and
capability-prepared agents. See README.md for the mapping to upstream source.
"""

from .agent import (
    Agent,
    GuardrailOutput,
    Handoff,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    OutputGuardrail,
    OutputGuardrailTripwireTriggered,
    handoff,
)
from .capabilities import CapableAgent, Compaction, Memory, Shell, Skills, Workspace
from .items import RunItem, assistant_message, function_call, function_call_output, user_message
from .model import ChatCompletionsModel, ModelResponse, ScriptedModel
from .run import (
    HandoffInputData,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelInputData,
    RunConfig,
    RunContext,
    Runner,
    RunResult,
    RunState,
)
from .session import InMemorySession, SessionSettings, SQLiteSession
from .tool import DEFAULT_REJECTION, DEFAULT_TOOL_ERROR, FunctionTool, function_tool
from .trimmer import ToolOutputTrimmer

__all__ = [
    "Agent",
    "CapableAgent",
    "ChatCompletionsModel",
    "Compaction",
    "DEFAULT_REJECTION",
    "DEFAULT_TOOL_ERROR",
    "FunctionTool",
    "GuardrailOutput",
    "Handoff",
    "HandoffInputData",
    "InMemorySession",
    "InputGuardrail",
    "InputGuardrailTripwireTriggered",
    "MaxTurnsExceeded",
    "Memory",
    "ModelBehaviorError",
    "ModelInputData",
    "ModelResponse",
    "OutputGuardrail",
    "OutputGuardrailTripwireTriggered",
    "RunConfig",
    "RunContext",
    "RunItem",
    "RunResult",
    "RunState",
    "Runner",
    "SQLiteSession",
    "ScriptedModel",
    "SessionSettings",
    "Shell",
    "Skills",
    "ToolOutputTrimmer",
    "Workspace",
    "assistant_message",
    "function_call",
    "function_call_output",
    "function_tool",
    "handoff",
    "user_message",
]
