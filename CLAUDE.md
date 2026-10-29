# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A collection of toy self-evolving agents, each exploring a different way an agent can improve itself. Each agent has its own `CLAUDE.md` with its architecture and commands; read it before working on that agent.

| Agent | How it evolves |
|---|---|
| [evolvingagent01/](evolvingagent01/) ([CLAUDE.md](evolvingagent01/CLAUDE.md)) | Writes new Python tools when none fits a task, and fixes or extends tools that fail. |
| [evolvingagent02/](evolvingagent02/) ([CLAUDE.md](evolvingagent02/CLAUDE.md)) | Learns SOP rules from a manager's feedback, testing each rule on the corrected case before saving it. |

When you add an agent, add a row here.

## Repo layout

Each agent is a self-contained Python package in its own top-level folder (`evolvingagentNN/`). The package folder holds everything specific to that agent: its modules, its notebook driver, `requirements.txt`, `docs/`, `tests/` and its own `CLAUDE.md`. Agents don't import from each other. Only `.env` is shared, at the repo root. It isn't committed; `env.example` is its template, so when a setting is added, add it there too.

## Conventions shared by all agents

- **Provider-independent design.** Agents must not depend on one LLM provider's special features (for example, built-in tool use). Ask for plain JSON in the reply instead. All provider-specific code lives in the agent's `llm.py`, which exposes `call_llm(system_prompt, user_prompt) -> str`. Comments, docstrings, prompts and docs say "the LLM", not a provider or model name.
- **Settings come from the root `.env`**, loaded with `python-dotenv`: `ANTHROPIC_API_KEY`, plus `DEFAULT_CLAUDE_MODEL` and `DEFAULT_MAX_TOKENS` for the model. Keep the existing variable names.
- **A Jupyter notebook in the agent's folder is the driver.** Jupyter starts the kernel in the notebook's folder, which is inside the package, so the notebook adds the repo root to `sys.path` before importing. It uses IPython's `%autoreload 2`, so edits take effect without restarting the kernel.
- **Run commands from the repo root**, so the agent packages are importable, e.g. `python -m pytest evolvingagentNN/tests`. Run each agent's tests separately: the agents share test file names, so one pytest run over several agents fails during collection.
- **Tests never call the real LLM API.** Each agent's `tests/conftest.py` adds the repo root to `sys.path`, sets a dummy API key, and provides a `fake_llm` fixture that replaces `llm.call_llm` with scripted replies. Modules import each other as modules (`from . import llm`) and call through them (`llm.call_llm(...)`), never `from .llm import call_llm`, so that one patch takes effect everywhere.
