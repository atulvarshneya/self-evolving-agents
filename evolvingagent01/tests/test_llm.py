import importlib

import dotenv
import pytest

from evolvingagent01 import llm


@pytest.fixture
def reload_agent(monkeypatch):
    """Reload the module so its import-time settings are re-read; restore it afterwards."""
    # Stop the real .env from being loaded so each test controls the environment.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    yield lambda: importlib.reload(llm)
    monkeypatch.undo()
    importlib.reload(llm)


def test_settings_come_from_env(monkeypatch, reload_agent):
    monkeypatch.setenv("DEFAULT_CLAUDE_MODEL", "claude-test-model")
    monkeypatch.setenv("DEFAULT_MAX_TOKENS", "1234")

    reload_agent()

    assert llm.MODEL_NAME == "claude-test-model"
    assert llm.MAX_TOKENS == 1234


def test_settings_fall_back_to_defaults(monkeypatch, reload_agent):
    monkeypatch.delenv("DEFAULT_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("DEFAULT_MAX_TOKENS", raising=False)

    reload_agent()

    assert llm.MODEL_NAME == "claude-sonnet-5-5"
    assert llm.MAX_TOKENS == 16000
