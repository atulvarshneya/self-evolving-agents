import json

from evolvingagent01 import evolver_agent, toolbox
from tool_code import tool_code

MISSING = {"status": "error", "fault": "capability", "type": "missing_capability",
           "message": "No existing tool can do this task.", "reason": "no weather tool"}

TOOL_ERROR = {"status": "error", "fault": "tool", "type": "tool_reported_error", "tool_name": "stocks",
              "arguments": {"symbols": ["^GSPC"]},
              "tool_source": "def execute():\n    return {'ok': False, 'error': {'type': 'auth'}}\n",
              "message": "The tool ran but reported that it could not produce the answer.",
              "error": {"type": "auth", "message": "HTTP Error 401: Unauthorized"}}

EXTEND = {"status": "error", "fault": "capability", "type": "extension_needed", "tool_name": "weather",
          "tool_source": tool_code("weather"), "reason": "needs a units argument"}


def _payload(filename, code=None):
    return json.dumps({"filename": filename, "code": code or tool_code(filename.removesuffix(".py"))})


def _all_files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def test_writes_tool_and_overwrites_existing(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("answer.py", "first version"),
                          _payload("answer.py", "second version")]

    evolver_agent.run_evolver("task", MISSING)
    evolver_agent.run_evolver("task", MISSING)

    assert (agent_workdir / "tools/generated/answer.py").read_text() == "second version"


def test_failure_context_is_sent_to_llm(agent_workdir, fake_llm):
    crash = {"status": "error", "fault": "tool", "type": "runtime_crash", "message": "boom",
             "trace": "Traceback ... ZeroDivisionError: division by zero"}
    fake_llm.responses = [_payload("answer.py")]

    evolver_agent.run_evolver("the task", crash)

    _, user_prompt = fake_llm.calls[0]
    assert "the task" in user_prompt
    assert "ZeroDivisionError: division by zero" in user_prompt


def test_non_json_reply_writes_nothing_and_does_not_raise(agent_workdir, fake_llm):
    before = _all_files(agent_workdir)
    fake_llm.responses = ["Sure! Here is your tool: def execute(): return 42"]

    evolver_agent.run_evolver("task", MISSING)

    assert _all_files(agent_workdir) == before


def test_prompt_states_both_protocols(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("answer.py")]

    evolver_agent.run_evolver("task", MISSING)

    system_prompt, _ = fake_llm.calls[0]
    assert toolbox.TOOL_SPEC_PROTOCOL in system_prompt
    assert toolbox.TOOL_PROTOCOL in system_prompt


def test_example_in_prompt_follows_the_spec_protocol(agent_workdir):
    # The example the Evolver copies from must itself pass the loader's checks
    toolbox.save_tool("add_numbers.py", evolver_agent._EXAMPLE_CODE)

    tool = toolbox.load_agent_tools()["add_numbers"]

    assert tool.spec_errors == []
    assert tool.execute(a=1, b=2) == {"ok": True, "result": 3}


def test_prompt_includes_failing_tool_code_arguments_and_error(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("stocks.py")]

    evolver_agent.run_evolver("task", TOOL_ERROR)

    _, user_prompt = fake_llm.calls[0]
    assert "Existing tool: 'stocks'" in user_prompt
    assert "```python\n" + TOOL_ERROR["tool_source"] in user_prompt
    assert "HTTP Error 401: Unauthorized" in user_prompt
    assert '"arguments": {"symbols": ["^GSPC"]}' in user_prompt
    # The code is shown once as readable source, not again as an escaped JSON string
    assert user_prompt.count("return {'ok': False") == 1


def test_fix_with_different_filename_replaces_the_failing_tool(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("stocks_v2.py")]

    path = evolver_agent.run_evolver("task", TOOL_ERROR)

    assert path.endswith("stocks.py")
    assert not (agent_workdir / "tools/generated/stocks_v2.py").exists()
    assert (agent_workdir / "tools/generated/stocks.py").exists()


# --- Modes ---

def test_missing_capability_uses_create_mode(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("weather.py")]

    evolver_agent.run_evolver("task", MISSING)

    assert "MODE: CREATE" in fake_llm.calls[0][1]


def test_tool_fault_uses_fix_mode(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("stocks.py")]

    evolver_agent.run_evolver("task", TOOL_ERROR)

    assert "MODE: FIX" in fake_llm.calls[0][1]


def test_extension_needed_uses_extend_mode_and_keeps_filename(agent_workdir, fake_llm):
    fake_llm.responses = [_payload("weather_with_units.py")]

    path = evolver_agent.run_evolver("task", EXTEND)

    user_prompt = fake_llm.calls[0][1]
    assert "MODE: EXTEND" in user_prompt
    assert "needs a units argument" in user_prompt
    assert path.endswith("weather.py")
