"""minideep — a minimal, dependency-free reproduction of the deepagents harness architecture."""

from minideep.backends import CompositeBackend, DirBackend, ShellBackend, StateBackend, supports_execution
from minideep.filesystem import FilesystemMiddleware
from minideep.graph import PatchToolCallsMiddleware, SubAgentSpec, create_deep_agent
from minideep.loop import Agent, final_text
from minideep.memory import MemoryMiddleware, SkillsMiddleware
from minideep.messages import AIMessage, Command, HumanMessage, SystemMessage, ToolCall, ToolMessage
from minideep.middleware import Middleware, ModelRequest, ModelResponse, ToolCallRequest
from minideep.model import ContextOverflowError, ScriptedModel, is_summary_request
from minideep.subagents import SubAgentMiddleware
from minideep.summarization import SummarizationMiddleware
from minideep.tools import Tool, ToolRuntime, tool

__all__ = [
    "AIMessage",
    "Agent",
    "Command",
    "CompositeBackend",
    "ContextOverflowError",
    "DirBackend",
    "FilesystemMiddleware",
    "HumanMessage",
    "MemoryMiddleware",
    "Middleware",
    "ModelRequest",
    "ModelResponse",
    "PatchToolCallsMiddleware",
    "ScriptedModel",
    "ShellBackend",
    "SkillsMiddleware",
    "StateBackend",
    "SubAgentMiddleware",
    "SubAgentSpec",
    "SummarizationMiddleware",
    "SystemMessage",
    "Tool",
    "ToolCall",
    "ToolCallRequest",
    "ToolMessage",
    "ToolRuntime",
    "create_deep_agent",
    "final_text",
    "is_summary_request",
    "supports_execution",
    "tool",
]
