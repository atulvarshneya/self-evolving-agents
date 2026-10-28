# CLAUDE.md: evolvingagent02

The Apprentice: a junior travel and expense assistant that evolves its **knowledge**, not its tools. It reviews expense reports by following the rules in its SOP ledger. When its manager corrects it, it turns the correction into one general rule, tests that the rule fixes the case it got wrong, and only then saves it.

The repo-wide conventions in the [root CLAUDE.md](../CLAUDE.md) apply here too. **[docs/DESIGN.md](docs/DESIGN.md) is the design reference**: the rule record format, every LLM reply shape and its checks, and the rule-testing loop. Update it in the same change as the code.

## Running

**[run_evolvingagent02.ipynb](run_evolvingagent02.ipynb) is the driver.** It imports `chat`, `show_sops`, `reset_memory` and `load_report` from `evolvingagent02.loop_harness`, with one cell per conversation turn of the demo script. Keep those functions' signatures and console output working for the notebook.

Dependencies are in [requirements.txt](requirements.txt), the same as `evolvingagent01`'s.

```bash
# From the repo root:
python -m pytest evolvingagent02/tests                                      # regression tests
python -m pytest evolvingagent02/tests/test_x.py::test_name                 # single test
python -c "from evolvingagent02 import loop_harness as h; h.chat('Review this.', h.load_report('tr-1042'))"
```

Memory lives in `memory/` (`sop_ledger.json`, `last_case.json`), found from the package's location (`sop_ledger.MEMORY_DIR`) and created on first use. Sample reports are in [samples/](samples/).

## Architecture

Imports only go downward: `llm` and `sop_ledger` import nothing from the project, `router`, `performer_agent` and `evolver_agent` import only those two, and `loop_harness` imports everything. Rule testing needs both agents, so it lives in `loop_harness`; neither agent imports the other.

| Module | Responsibility |
|---|---|
| [llm.py](llm.py) | Copy of `evolvingagent01/llm.py`: `call_llm()`, `parse_json()` |
| [sop_ledger.py](sop_ledger.py) | Rule records, `SEED_RULES`, load/save, `apply_change()` (pure: returns a candidate ledger), `active_rules()`/`format_rules()`, `check_decisions()`, the last case |
| [router.py](router.py) | `route_message()`: "task" or "feedback". A report means a task; unreadable replies fall back to a task |
| [performer_agent.py](performer_agent.py) | `run_performer()`: review a report or answer a question with the active rules; retries replies that fail `check_decisions()` |
| [evolver_agent.py](evolver_agent.py) | `propose_change()`: one `add` / `update` / `none` proposal; retries replies that fail `check_proposal()` |
| [loop_harness.py](loop_harness.py) | `chat()`, `show_sops()`, `reset_memory()`, `load_report()`; saves the last case; tests and saves rules |

Key constraints:
- The LLM never writes the ledger or picks rule IDs. It proposes one change; `apply_change()` assigns `R<n>` IDs (never reused) and links superseded rules both ways.
- A rule correcting a case is saved only if re-running that case with it gives the `expected` decisions, after up to `MAX_RULE_REVISIONS = 2` revisions. A policy statement with no case is saved with `verified: false`.
- Only the harness saves the last case, so the test re-run doesn't overwrite it. After a rule is saved, the last case is updated with the re-run's decisions.
- Every LLM reply goes through `llm.parse_json()` and a JSON Schema check. Bad replies never raise.

## Tests

One test file per module. The `memory_dir` fixture is autouse, so every test gets a temporary `MEMORY_DIR` and none touches the real `memory/`. `fake_llm` replaces `llm.call_llm` with scripted responses (set `fake_llm.responses`, inspect `fake_llm.calls`); remember a feedback message with no report makes a router call first. [tests/replies.py](tests/replies.py) builds the scripted replies: `route()`, `review()`, `proposal()`, `expect()`, plus a sample `REPORT`.
