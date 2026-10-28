import os
import sys

import pytest

# tests/ lives inside the evolvingagent01 package; the repo root above it must be importable
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

# llm builds an Anthropic client at import time, which needs a key to exist.
# Tests never hit the real API, so a dummy value is enough.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from evolvingagent01 import llm, toolbox  # noqa: E402


@pytest.fixture
def agent_workdir(tmp_path, monkeypatch):
    """Point the agent's tools folder at <tmp_path>/tools so tests never touch the real one."""
    monkeypatch.setattr(toolbox, "TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.chdir(tmp_path)
    toolbox.setup_environment()
    return tmp_path


@pytest.fixture
def write_tool(agent_workdir):
    """Write a tool file relative to the temp workdir, e.g. write_tool("tools/generated/x.py", code)."""

    def _write(rel_path, code):
        path = agent_workdir / rel_path
        path.write_text(code)
        return path

    return _write


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace llm.call_llm with scripted responses: append strings to fake_llm.responses.

    Both agents call llm.call_llm through the module, so this one patch covers them.
    """

    class FakeLLM:
        def __init__(self):
            self.responses = []
            self.calls = []

        def __call__(self, system_prompt, user_prompt):
            self.calls.append((system_prompt, user_prompt))
            return self.responses.pop(0)

    fake = FakeLLM()
    monkeypatch.setattr(llm, "call_llm", fake)
    return fake
