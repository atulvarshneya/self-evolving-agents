# Self-Evolving Agents

A collection of small, readable agents, each exploring a different way an LLM-based agent can improve itself while it works. They are toys: each one isolates a single idea so you can see how it behaves, read the whole thing in one sitting, and experiment with it in a notebook.

| Agent | How it evolves |
|---|---|
| [evolvingagent01](evolvingagent01/) | **Grows its own toolbox.** When no tool fits a task, it writes a new Python tool, loads it and runs it. Tools that fail are fixed, and tools that are close but missing something are extended. |
| [evolvingagent02](evolvingagent02/) | **Learns from feedback.** An apprentice expense clerk that reviews expense reports using a ledger of Standard Operating Procedure (SOP) rules. When its manager corrects it, it turns the correction into a general rule, tests that the rule fixes the case it got wrong, and only then saves it. |

## How they work

### evolvingagent01: the tool builder

A **Performer** asks the LLM which tool to call for a task and with what arguments, then runs it. If the call fails, the failure is classified:

- **caller fault:** the LLM called the tool wrongly, so the Performer retries
- **tool fault:** the tool is broken, so it's fixed
- **capability fault:** no tool can do the task, so a tool is created or extended

An **Evolver** then writes the new or corrected tool to disk, and the loop runs again with it. Every tool declares its arguments and result as JSON Schema and returns a standard success or error shape, so the agent can tell bad calls from bad tools. See [docs/TOOL_PROTOCOL.md](evolvingagent01/docs/TOOL_PROTOCOL.md).

### evolvingagent02: the apprentice

Each message from the manager is first routed as a **task** (review a report, answer a question) or **feedback** (a correction or a new policy).

- **Tasks** go to the Performer, which gives an approve / flag for review / deny decision for each item, citing the rules it applied.
- **Feedback** goes to the Evolver, which proposes exactly one change: add a rule, update (supersede) an existing one, or make no change.

The harness re-runs the corrected case with the proposed rule and saves it only if the decisions come out as the manager wanted. If they don't, the Evolver revises the rule and tries again. The LLM never edits the ledger directly or chooses rule IDs; code does. See [docs/DESIGN.md](evolvingagent02/docs/DESIGN.md).

## Getting started

Requirements: Python 3.11+ and an API key for the LLM.

```bash
git clone https://github.com/atulvarshneya/self-evolving-agents.git
cd Self-Evolving-Agents
python -m venv .venv
source .venv/bin/activate
pip install -r evolvingagent01/requirements.txt -r evolvingagent02/requirements.txt
pip install jupyter      # to run the notebook drivers
```

Create a `.env` file in the repo root. It is shared by all agents and is ignored by git:

```bash
ANTHROPIC_API_KEY=your-key-here
DEFAULT_CLAUDE_MODEL=claude-sonnet-5-5   # optional
DEFAULT_MAX_TOKENS=16000                 # optional
```

### Running an agent

Each agent is driven from the Jupyter notebook in its folder:

- [evolvingagent01/run_evolvingagent01.ipynb](evolvingagent01/run_evolvingagent01.ipynb): pick one of the sample tasks (or write your own) and call `main(target_task=...)`.
- [evolvingagent02/run_evolvingagent02.ipynb](evolvingagent02/run_evolvingagent02.ipynb): run the cells in order for the demo. It reviews a report, takes the manager's feedback, learns a rule, applies it to a new report, and then supersedes the rule when the policy changes.

The notebooks use `%autoreload 2`, so edits to an agent's code take effect without restarting the kernel.

### Running the tests

The tests never call the real LLM; they use scripted replies. Run them from the repo root, one agent at a time:

```bash
python -m pytest evolvingagent01/tests
python -m pytest evolvingagent02/tests
```

## Repository layout

```
Self-Evolving-Agents/
  .env                  shared settings (not committed)
  CLAUDE.md             guidance for Claude Code: shared conventions and the agent index
  evolvingagentNN/      one self-contained package per agent
    *.py                the agent's modules
    run_evolvingagentNN.ipynb
    requirements.txt
    docs/  tests/  CLAUDE.md
```

Agents never import from each other, so each folder can be read and changed on its own.

## Design principles

- **Provider-independent.** Agents don't rely on any provider's built-in tool-use feature; they ask the LLM for plain JSON and check it with JSON Schema. All provider-specific code is in each agent's `llm.py`, which currently uses the Anthropic API. To use another provider, rewrite `call_llm(system_prompt, user_prompt) -> str` there.
- **Never trust the LLM's reply.** Every reply is parsed and validated. Malformed replies are retried with the errors included in the prompt, and never crash the agent or corrupt its saved state.
- **Changes are checked before they're kept.** A new tool must run and return a valid result; a new rule must fix the case it was learned from.
- **Small and readable.** Each agent is a handful of short modules, with a notebook driver and a regression test suite.

## ⚠️ Safety

`evolvingagent01` executes code written by the LLM **in-process, with no sandboxing**, and its tools can read and write files and access the network. Run it only on a machine and account where that is acceptable, and review generated tools in `evolvingagent01/tools/generated/` if in doubt.
