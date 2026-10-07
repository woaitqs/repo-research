import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture
def run():
    """Run a coroutine to completion (keeps the test suite free of async plugins)."""
    return asyncio.run


def outputs(call):
    """Tool outputs that reached the model in one recorded call."""
    return [i["output"] for i in call.input if i.get("type") == "function_call_output"]


def kinds(call):
    from miniagents.items import kind

    return [kind(i) for i in call.input]
