import json

from evolvingagent01 import loop_harness
from tool_code import tool_code

NEW_TOOL = json.dumps({"action": "new_tool", "reason": "nothing fits"})
CITY = {"city": {"type": "string"}}


def _use(tool_name, arguments=None):
    return json.dumps({"action": "use_tool", "tool_name": tool_name, "arguments": arguments or {}})


def _payload(filename, code):
    return json.dumps({"filename": filename, "code": code})


def _returns(name, result_literal, **kwargs):
    """Code for a protocol-following tool that succeeds with the given Python literal."""
    return tool_code(name, returns=f"{{'ok': True, 'result': {result_literal}}}", **kwargs)


def _evolver_calls(fake_llm):
    return [c for c in fake_llm.calls if c[0].startswith("You are a software engineer who writes tools")]


def test_missing_tool_is_created_then_called_with_arguments(agent_workdir, fake_llm, capsys):
    fake_llm.responses = [NEW_TOOL,
                          _payload("weather.py", _returns("weather", "'sunny in ' + city", inputs=CITY)),
                          _use("weather", {"city": "Cupertino"})]

    loop_harness.main("weather in Cupertino")

    assert "Final Code Output: sunny in Cupertino" in capsys.readouterr().out
    assert fake_llm.responses == []


def test_dict_output_is_pretty_printed(agent_workdir, fake_llm, capsys):
    fake_llm.responses = [NEW_TOOL,
                          _payload("weather.py", _returns("weather", "{'city': 'Cupertino', 'temp_f': 61.2}")),
                          _use("weather")]

    loop_harness.main("weather")

    out = capsys.readouterr().out
    assert 'Final Code Output: \n{\n  "city": "Cupertino",\n  "temp_f": 61.2\n}' in out


def test_crashing_tool_is_rewritten_then_succeeds(agent_workdir, fake_llm, capsys):
    fake_llm.responses = [NEW_TOOL,
                          _payload("answer.py", tool_code("answer", body="return 1 / 0")),
                          _use("answer"),
                          _payload("answer.py", _returns("answer", "'fixed answer'")),
                          _use("answer")]

    loop_harness.main("give me an answer")

    out = capsys.readouterr().out
    assert "Execution crashed inside tool" in out
    assert "Final Code Output: fixed answer" in out
    assert "runtime_crash" in _evolver_calls(fake_llm)[1][1]


def test_gives_up_after_max_generations(agent_workdir, fake_llm, capsys):
    fake_llm.responses = [NEW_TOOL, _payload("t1.py", _returns("t1", "1")),
                          NEW_TOOL, _payload("t2.py", _returns("t2", "2")),
                          NEW_TOOL, _payload("t3.py", _returns("t3", "3")),
                          NEW_TOOL]

    loop_harness.main("impossible task")

    assert len(_evolver_calls(fake_llm)) == 3
    assert "Loop halted" in capsys.readouterr().out


def test_tool_written_in_last_generation_is_tried(agent_workdir, fake_llm, capsys):
    fake_llm.responses = [NEW_TOOL, _payload("t1.py", _returns("t1", "1")),
                          NEW_TOOL, _payload("t2.py", _returns("t2", "2")),
                          NEW_TOOL, _payload("t3.py", _returns("t3", "3")),
                          _use("t3")]

    loop_harness.main("needs three tries")

    out = capsys.readouterr().out
    assert "Final Code Output: 3" in out
    assert "Loop halted" not in out


# --- Tool result protocol ---

def test_tool_reporting_an_error_is_fixed_in_place(agent_workdir, fake_llm, capsys):
    failing = tool_code("stocks", returns="{'ok': False, 'error': {'type': 'auth', 'message': 'HTTP Error 401'}}")
    fake_llm.responses = [NEW_TOOL,
                          _payload("stocks.py", failing),
                          _use("stocks"),
                          _payload("stocks.py", _returns("stocks", "{'S&P 500': 5800.1}")),
                          _use("stocks")]

    loop_harness.main("stock indices")

    out = capsys.readouterr().out
    assert "SUCCESS" in out and '"S&P 500": 5800.1' in out
    fix_prompt = _evolver_calls(fake_llm)[1][1]
    assert "tool_reported_error" in fix_prompt
    assert "HTTP Error 401" in fix_prompt
    assert failing in fix_prompt


# --- Tool spec protocol ---

def test_legacy_tool_without_spec_is_rewritten(agent_workdir, write_tool, fake_llm, capsys):
    write_tool("tools/generated/legacy.py", "def execute():\n    return 328")
    fake_llm.responses = [_use("legacy"),
                          _payload("legacy.py", _returns("legacy", "328")),
                          _use("legacy")]

    loop_harness.main("sum of primes below 50")

    assert "Final Code Output: 328" in capsys.readouterr().out
    fix_prompt = _evolver_calls(fake_llm)[0][1]
    assert "spec_violation" in fix_prompt
    assert "MODE: FIX" in fix_prompt


def test_tool_is_extended_then_called_with_new_argument(agent_workdir, write_tool, fake_llm, capsys):
    write_tool("tools/generated/weather.py", _returns("weather", "'61F'", inputs=CITY))
    units = {**CITY, "units": {"type": "string"}}
    fake_llm.responses = [json.dumps({"action": "extend_tool", "tool_name": "weather", "reason": "needs units"}),
                          _payload("weather.py", _returns("weather", "'16C' if units == 'c' else '61F'",
                                                          inputs=units, required=["city"])),
                          _use("weather", {"city": "Cupertino", "units": "c"})]

    loop_harness.main("weather in Cupertino in Celsius")

    assert "Final Code Output: 16C" in capsys.readouterr().out
    assert "MODE: EXTEND" in _evolver_calls(fake_llm)[0][1]


def test_calling_mistakes_skip_the_evolver(agent_workdir, write_tool, fake_llm, capsys):
    write_tool("tools/generated/answer.py", _returns("answer", "42"))
    fake_llm.responses = ["not json"] * 3 + [_use("answer")]

    loop_harness.main("give me 42")

    out = capsys.readouterr().out
    assert "moving to the next generation" in out
    assert "Final Code Output: 42" in out
    assert _evolver_calls(fake_llm) == []
