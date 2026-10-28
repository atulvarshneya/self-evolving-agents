# CLAUDE.md: evolvingagent01

A self-evolving agent that grows its own toolbox. When the LLM finds no tool that solves a task, it writes a new Python tool to disk, which is then hot-loaded and run. Tools that fail are fixed, and tools that are close but missing something are extended.

The repo-wide conventions in the [root CLAUDE.md](../CLAUDE.md) apply here too. [docs/DESIGN.md](docs/DESIGN.md) explains the design and the reasons behind it; keep it in step with the code.

## Running

**[run_evolvingagent01.ipynb](run_evolvingagent01.ipynb) is the actual driver for the agent.** It holds the sample `tasks` list and calls `main(target_task=tasks[i])`, imported with `from evolvingagent01.loop_harness import main`. Keep `main()`'s signature and console output working for the notebook. Tasks that write files "in the current folder" write them here, in `evolvingagent01/`, because that's where the notebook's kernel runs.

There is no build or lint config. Dependencies are in [requirements.txt](requirements.txt): `anthropic`, `python-dotenv`, `jsonschema`, plus `pytest` for tests.

```bash
# From the repo root:
python -m pytest evolvingagent01/tests                                      # regression tests
python -m pytest evolvingagent01/tests/test_x.py::test_name                 # single test
python -c "from evolvingagent01 import loop_harness; loop_harness.main(target_task='...')"   # quick run outside the notebook
python evolvingagent01/tools/generated/<tool>.py                            # run one generated tool directly
```

The tools folder is found from the package's own location (`toolbox.TOOLS_DIR`), not the current directory.

## Architecture

Five modules, with the tools in [tools/](tools/). Imports only go downward: `llm` and `toolbox` import nothing from the project, the two agents import those two, and `loop_harness` imports everything.

| Module | Responsibility |
|---|---|
| [llm.py](llm.py) | `.env` loading, the Anthropic client, `MODEL_NAME`/`MAX_TOKENS`, `call_llm()`, `parse_json()` |
| [toolbox.py](toolbox.py) | What a tool is and where tools live: the protocol texts and their checks (`validate_spec()`, `check_arguments()`, `check_result()`, `check_output()`), `describe_tools()`, `setup_environment()`, `load_agent_tools()`, `save_tool()` |
| [performer_agent.py](performer_agent.py) | `run_performer()`: choose a tool and its arguments, run it, attribute faults, retry calling mistakes |
| [evolver_agent.py](evolver_agent.py) | `run_evolver()`: create, fix or extend a tool |
| [loop_harness.py](loop_harness.py) | `main()`: the generation loop, and output formatting |

`main()` runs repeated "generation" cycles. Each cycle does the following:

1. **`toolbox.load_agent_tools()`**: scans `tools/*.py` (hand-written tools) and `tools/generated/*.py` (LLM-written tools). It imports each one fresh from its file path every time (so rewritten tools are picked up), and returns `{name: toolbox.Tool}` for any module that exposes `execute()`. A `Tool` holds `execute`, `spec`, `source` and `spec_errors`. `setup_environment()` runs first, creates the tool folders if they are missing, and **deletes generated tools older than 24 hours** (by file mtime).
2. **`run_performer()`**: shows the LLM each tool's `SPEC` and gets back JSON choosing `use_tool` (with `arguments`), `extend_tool` or `new_tool`. It checks the arguments, runs `execute(**arguments)`, checks the result, and returns a report with a `fault` of `caller`, `tool` or `capability`. Calling mistakes (`fault: caller`) are retried inside the Performer, up to `MAX_CALL_RETRIES = 2` times.
3. **`run_evolver()`**: runs for tool and capability faults, in create, fix or extend mode. The LLM returns JSON `{"filename", "code"}`, which `toolbox.save_tool()` writes to `tools/generated/`. A fix or extension always goes to `<tool_name>.py`, so it replaces the old version. Caller faults that survive the retries skip the Evolver.

### Tool protocol

**[docs/TOOL_PROTOCOL.md](docs/TOOL_PROTOCOL.md) is the reference for how tools and agents interact.** It covers the `SPEC` format, the result shape, the routing reply, the full fault table and the Evolver modes. Read it before changing any of `toolbox.py`, `performer_agent.py` or `evolver_agent.py`, and update it in the same change. In short:
- Every tool defines `SPEC = {name, description, input_schema, output_schema}`, with the schemas in JSON Schema, and an `execute()` whose parameters match `input_schema`. Every tool returns `{"ok": True, "result": ...}` or `{"ok": False, "error": {...}}`.
- The protocol text lives in `toolbox.TOOL_SPEC_PROTOCOL` / `toolbox.TOOL_PROTOCOL` (pasted into the Evolver's prompt), and the checks enforcing it sit next to it in `toolbox.py`. Change a rule's text and its check together.
- Tools that break the spec are kept, flagged `needs_repair`, and repaired by the Evolver the first time they are chosen. That includes old tools with no `SPEC`.

Key constraints that come from this design:
- Tool calls come back as plain JSON in the LLM's reply, not through a provider's built-in tool use.
- Both LLM responses go through `llm.parse_json()`, which strips code fences. Bad replies never raise.
- `save_tool()` only accepts a bare module name for the filename (adding `.py` if missing). Anything with a path or that isn't a valid identifier is rejected.
- `main()` allows up to 3 Evolver runs, with one more Performer pass at the end so the last tool written still gets tried.
- `call_llm()` is the single wrapper around the Anthropic API. `MODEL_NAME`/`MAX_TOKENS` fall back to `claude-sonnet-5-5`/16000 when not set in `.env`. `.env` is loaded with `override=True`, so it wins over shell variables. It also uses adaptive thinking and `effort: high`.
- Generated code is executed in-process with no sandboxing.

## Tests

There is one test file per module (`tests/test_<module>.py`). Besides never calling the real API, tests must never touch the real tools folder. Use the fixtures in [tests/conftest.py](tests/conftest.py): `agent_workdir` points `toolbox.TOOLS_DIR` at an empty temp directory (any test that loads, saves or cleans up tools must use it), `write_tool` writes a tool file there, and `fake_llm` replaces `llm.call_llm` with scripted responses (set `fake_llm.responses`, inspect `fake_llm.calls`). [tests/tool_code.py](tests/tool_code.py) builds tools that follow the protocol: `tool_code()` for files, `make_tool()` for in-memory `Tool`s.
