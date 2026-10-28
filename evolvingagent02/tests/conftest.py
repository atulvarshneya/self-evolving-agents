import os
import sys

import pytest

# tests/ lives inside the evolvingagent02 package; the repo root above it must be importable
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

# llm builds an Anthropic client at import time, which needs a key to exist.
# Tests never hit the real API, so a dummy value is enough.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from evolvingagent02 import llm, sop_ledger  # noqa: E402


@pytest.fixture(autouse=True)
def memory_dir(tmp_path, monkeypatch):
    """Point the agent's memory folder at <tmp_path>/memory so tests never touch the real one."""
    path = tmp_path / "memory"
    monkeypatch.setattr(sop_ledger, "MEMORY_DIR", str(path))
    return path


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace llm.call_llm with scripted responses: append strings to fake_llm.responses.

    Every module calls llm.call_llm through the module, so this one patch covers them all.
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
