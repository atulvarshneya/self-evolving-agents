# evolvingagent01: the Tool Builder (design)

`evolvingagent01` evolves by growing its own **toolbox**. Given a task, it looks for a tool that can do it. If there isn't one, it writes a new Python tool, loads it and runs it. If a tool fails, it fixes it; if a tool is close but missing something, it extends it. Tools persist between runs, so later tasks can reuse what earlier ones built.

```
                 [ Task ]
                    │
                    ▼
   ┌──────> [ load_agent_tools() ] <──────── tools/*.py, tools/generated/*.py
   │                │ {name: Tool}
   │                ▼
   │          [ Performer ] ── choose tool + arguments (LLM) ── run it ── check the result
   │                │
   │      ┌─────────┼─────────────────────────┬──────────────────────────┐
   │   success   caller fault           tool fault                 capability fault
   │      │      (retried inside the    (crash, bad result,        (no tool fits, or
   │      ▼       Performer first)       broken spec)               one needs extending)
   │   [ done ]       │                      │                          │
   │                  │                      ▼                          ▼
   │                  │               [ Evolver: fix ]        [ Evolver: create / extend ]
   │                  │                      │                          │
   │                  │                      └────── tools/generated/<name>.py
   └──────────────────┴─────────── next generation ───────────────────────┘
```

This document explains how the agent is put together and why. The exact contract between the Evolver and the Performer (the `SPEC` format, the result shape, the routing reply and the full fault table) is in **[TOOL_PROTOCOL.md](TOOL_PROTOCOL.md)**; this document summarizes it rather than repeating it. The repo-wide conventions are in the [root CLAUDE.md](../../CLAUDE.md).

## 1. Goal and scenario

The agent is given one task in plain language, for example:

- *"Compute the sum of all prime numbers less than 50."*
- *"Please let me know the current weather in Cupertino, CA. Don't assume the lat/long, do an actual lookup."*
- *"Create a Microsoft Word file, hw.docx, in the current folder, with the first line as Heading 1 'Greetings' and the second line 'Hello, World'."*

It starts with an empty toolbox. On the first task it writes a tool, for example `sum_primes_in_range(lower, upper)`, runs it, and returns the answer. A later, similar task (*"sum the primes below 1000"*) reuses the same tool with different arguments instead of writing another one. If the tool crashes or returns a wrong-shaped result, the agent rewrites it in place.

The idea being explored: **an agent whose capabilities grow from its own failures**, with enough structure that it can tell *what* failed (its own call, the tool, or a missing capability) and respond to each appropriately.

## 2. Key design decisions

| Decision | Why |
|---|---|
| **Tool calls come back as plain JSON** in the LLM's reply, not through a provider's built-in tool-use feature | Keeps the agent provider-independent. Any LLM that can follow instructions and write JSON will do; all provider code is in `llm.py`. |
| **Every tool declares a `SPEC`** with JSON Schema for its arguments and its result | The Performer can show the LLM exactly how to call each tool, check the arguments before running it, and check the result afterwards. |
| **Every tool returns `{"ok": true, "result": ...}` or `{"ok": false, "error": {...}}`** | A tool that fails says so explicitly, instead of the agent guessing from an exception or an error string hidden in a "result". |
| **Each failure is attributed a `fault`: caller, tool or capability** | Different faults need different responses. Rewriting a tool because the LLM passed the wrong arguments would make things worse. |
| **Caller faults are retried inside the Performer** (`MAX_CALL_RETRIES = 2`), and escalate to a tool fault if they keep happening | Calling mistakes are cheap to fix by asking again with the errors. A tool that keeps being called wrongly probably has an unclear interface, so it goes to the Evolver. |
| **Tools are made general**: anything task-specific becomes an argument | Lets tools be reused across similar tasks, which is what makes the toolbox grow usefully instead of filling up with one-off scripts. |
| **Fix and extend keep the tool's interface**, and always overwrite `<tool_name>.py` | Existing calls keep working, and the old, broken version doesn't linger alongside the new one. |
| **Tools that break the spec are kept and flagged `needs_repair`**, not dropped | The Evolver can repair them when they are next chosen. This is also how tools written before the spec existed get upgraded. |
| **Tools are loaded fresh from their file path every generation** | A rewritten tool is picked up immediately, without Python's import cache serving the old version. |
| **The Evolver doesn't check its own output**; the loader and Performer do, next generation | One place enforces the protocol. A bad tool just becomes the next generation's tool fault. |

## 3. Package layout

`evolvingagent01` is a self-contained package. It shares only the root `.env` with the other agents.

```
evolvingagent01/
  __init__.py
  llm.py               settings from .env, the LLM client, call_llm(), parse_json()
  toolbox.py           the protocol texts and checks, the Tool record, loading, saving and cleanup of tools
  performer_agent.py   run_performer(): choose a tool and arguments, run it, attribute faults, retry calling mistakes
  evolver_agent.py     run_evolver(): create, fix or extend a tool
  loop_harness.py      main(): the generation loop and output formatting
  tools/               hand-written tools (*.py)
    generated/         tools written by the Evolver (deleted after 24 hours)
  run_evolvingagent01.ipynb
  docs/DESIGN.md  docs/TOOL_PROTOCOL.md  requirements.txt  tests/  CLAUDE.md
```

**Import order:** imports only go downward. `llm` and `toolbox` import nothing from the project. `performer_agent` and `evolver_agent` import those two, and never each other. `loop_harness` imports everything. The harness is the only place that knows about both agents.

**Module imports:** modules import each other as modules (`from . import llm`) and call through them (`llm.call_llm(...)`), so the tests' `fake_llm` patch reaches every caller.

**Dependencies:** `anthropic` (used only in `llm.py`), `python-dotenv`, `jsonschema`, and `pytest` for tests.

## 4. The LLM wrapper (`llm.py`)

The only provider-specific module.

- Loads the root `.env` with `override=True`, so it wins over shell variables.
- `MODEL_NAME` and `MAX_TOKENS` come from `DEFAULT_CLAUDE_MODEL` and `DEFAULT_MAX_TOKENS`, with defaults when unset.
- **`call_llm(system_prompt, user_prompt) -> str`** makes one request and returns the reply text.
- **`parse_json(text)`** parses a reply into a dict, tolerating a ```` ```json ```` code fence. It raises `ValueError` for anything else, which every caller catches; a bad reply never crashes the agent.

Switching providers means rewriting `call_llm()` and nothing else.

## 5. The toolbox (`toolbox.py`)

Everything about what a tool *is* and where tools *live*.

**The protocol.** `TOOL_SPEC_PROTOCOL` and `TOOL_PROTOCOL` are the rules as text, pasted into the Evolver's prompt. The functions that enforce them sit next to them, so a rule's text and its check change together:

| Check | Run by | Enforces |
|---|---|---|
| `validate_spec(name, module)` | the loader | `SPEC` has the four keys, `name` matches the filename, both schemas are valid JSON Schema, and `input_schema` matches `execute()`'s parameters exactly |
| `check_arguments(spec, arguments)` | the Performer, before running | arguments match `input_schema` |
| `check_result(result)` | the Performer, after running | the return value is a success, an error, or a protocol violation |
| `check_output(spec, result)` | the Performer, on success | `result` matches `output_schema` |

**The `Tool` record** holds `execute`, `spec`, `source` (the module's code, sent to the Evolver for fixes) and `spec_errors` (empty for a valid tool).

**`describe_tools(tools)`** is what the Performer's LLM sees: each valid tool's `SPEC`, or `{name, status: "needs_repair", description}` for a tool with spec errors.

**Where tools live.** `TOOLS_DIR` is `tools/` next to the package, not relative to the current folder, so the agent works however it's started. Tests point it at a temporary folder.

- **`setup_environment()`** creates `tools/` and `tools/generated/` if needed, and deletes generated tools whose files are more than 24 hours old. This keeps the toy's toolbox from growing without bound; hand-written tools in `tools/` are never deleted.
- **`load_agent_tools()`** imports every `*.py` in `tools/`, then `tools/generated/`, and returns `{name: Tool}` for each module with an `execute()`. Each file is executed fresh from its path (`importlib.util.spec_from_file_location`) and registered in `sys.modules` under `evolvingagent01.tools[.generated].<name>`. Generated tools load second, so a generated fix replaces a hand-written tool of the same name. A file that fails to import is skipped with a warning; the rest still load.
- **`save_tool(filename, code)`** writes to `tools/generated/`. It accepts only a bare module name (adding `.py` if missing) and rejects anything with a path or that isn't a valid Python identifier, so LLM-chosen filenames can't escape the folder or produce an unimportable module.

## 6. The Performer (`performer_agent.py`)

`run_performer(task, available_tools)` returns either a success report `{"status": "success", "output", "tool_name", "arguments"}` or a failure report with a `fault`.

Each attempt:
1. Sends the LLM the task and `describe_tools()`. The LLM replies with one of `use_tool` (with `arguments`), `extend_tool` or `new_tool`.
2. For `new_tool` or `extend_tool`, returns a **capability** fault.
3. For `use_tool`: refuses to run a `needs_repair` tool (**tool** fault, `spec_violation`); checks the arguments (**caller** fault if invalid); runs `execute(**arguments)`; and classifies the outcome: a crash, a non-protocol return value, a result that breaks `output_schema`, or a reported error are **tool** faults, except a `bad_input` error, which means the arguments were wrong and is a **caller** fault.

Caller faults are retried up to `MAX_CALL_RETRIES = 2` times, each retry prompt listing every previous failed attempt. If the retries run out, repeated `invalid_arguments` or `bad_input` escalate to a tool fault (the tool's interface is probably the problem); an unparseable reply or an unknown tool stays a caller fault, and there's nothing for the Evolver to fix. A second `bad_input` escalates immediately.

Every report about a specific tool carries its name, spec, spec errors, source code and the arguments used, so the Evolver has everything it needs to work on that tool. The full table of failure types is in [TOOL_PROTOCOL.md §4](TOOL_PROTOCOL.md#4-fault-model).

## 7. The Evolver (`evolver_agent.py`)

`run_evolver(task, failure_context)` returns the saved file path, or `None` if nothing was written.

The mode comes from the failure report's `type`:

| Mode | When | The LLM is told to |
|---|---|---|
| **create** | `missing_capability` | write a general tool, taking anything task-specific as an argument |
| **fix** | any tool fault | stop the failure without changing `input_schema`, so existing calls keep working (except for a `spec_violation`, where the spec may change as much as needed to follow the protocol) |
| **extend** | `extension_needed` | keep every existing parameter, and add new ones only as optional parameters with defaults |

The prompt contains both protocol texts, a complete example tool (`add_numbers`) that passes the loader's checks, the task, the failure report, and, for fix and extend, the tool's current code. The rules also restrict tools to the Python standard library and to data sources that need no API keys, since there's no way to give a generated tool credentials.

The LLM replies `{"filename", "code"}`. For fix and extend, the filename is forced to `<tool_name>.py`, whatever the LLM chose, so the new version replaces the old one. A reply that can't be parsed writes nothing.

## 8. The loop harness and notebook (`loop_harness.py`)

`main(target_task)` runs up to 3 **generations**, plus one final Performer pass so the last tool written still gets tried:

1. `load_agent_tools()`, picking up anything the Evolver wrote last generation.
2. `run_performer()`.
3. On success, print the output (dicts and lists as indented JSON) and stop.
4. On a tool or capability fault, `run_evolver()`.
5. On a caller fault that survived the retries, go straight to the next generation; there's no tool to fix.

If every generation fails, the loop stops with a message.

The driver is **`run_evolvingagent01.ipynb`**. It adds the repo root to `sys.path` (the kernel starts inside the package folder), enables `%autoreload 2` so code edits take effect without restarting the kernel, holds a list of sample tasks, and calls `main(target_task=tasks[i])`. Tasks that write files "in the current folder" write them into `evolvingagent01/`, where the kernel runs.

## 9. Tests (`tests/`)

One test file per module. Tests never call the real LLM API and never touch the real `tools/` folder.

- **`conftest.py`** adds the repo root to `sys.path`, sets a dummy API key, and provides:
  - `agent_workdir`: points `toolbox.TOOLS_DIR` at a temporary folder
  - `write_tool`: writes a tool file there
  - `fake_llm`: replaces `llm.call_llm` with scripted replies
- **`tool_code.py`** builds tools that follow the protocol: `tool_code()` for tool files, `make_tool()` for in-memory `Tool`s.
- **`test_llm.py`:** settings come from `.env`, with defaults.
- **`test_toolbox.py`:** loading from both folders, skipping broken files, reloading rewritten tools, 24-hour cleanup, filename safety, every `validate_spec()` rule, the argument, result and output checks, and `describe_tools()`.
- **`test_performer.py`:** each routing action, each failure type and its fault, retries with feedback, and escalation to a tool fault.
- **`test_evolver.py`:** the prompt's contents, choosing the mode, overwriting the failing tool, and unparseable replies.
- **`test_loop_harness.py`:** end-to-end runs: create then call, crash then fix, fix in place, upgrade a tool with no spec, extend then call with the new argument, caller faults skipping the Evolver, the final extra pass, and giving up.

## 10. Limitations and possible later work

- **No sandboxing.** Generated code runs in the agent's own process with full access to files and the network. Running tools in a subprocess or container is the obvious next step for anything beyond a toy.
- **No checking of correctness.** A tool that returns a well-formed but wrong answer counts as a success. Asking the LLM to write tests alongside each tool, or to judge the result against the task, would close this gap.
- **Time-based cleanup.** Generated tools are deleted after 24 hours however useful they were. Keeping tools that have succeeded, or tracking usage, would be smarter.
- **One task at a time.** There is no planning: a task that needs two tools chained together has to be solved by one tool.
- **The toolbox grows flat.** There's no merging of similar tools or retiring of unused ones, and every tool's spec goes into every Performer prompt.
