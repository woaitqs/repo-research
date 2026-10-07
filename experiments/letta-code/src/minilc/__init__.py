"""minilc: a minimal, stdlib-only reproduction of letta-code's architecture.

The idea reproduced: a *stateful backend* that runs exactly one model step per
run and stops whenever the model wants a tool, plus a *client harness* that
executes tools locally and resumes the backend with an approval message.
Memory is a git repository compiled into the system prompt from HEAD.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
