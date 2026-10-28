import json

from evolvingagent01 import performer_agent, toolbox
from tool_code import make_tool, tool_code

CITY = {"city": {"type": "string"}}


def _use(tool_name, arguments=None):
    return json.dumps({"action": "use_tool", "tool_name": tool_name, "arguments": arguments or {}})


def _ok(result):
    return lambda **kwargs: {"ok": True, "result": result}


def _bad_input(message="Unknown city"):
    return {"ok": False, "error": {"type": "bad_input", "message": message}}


def test_runs_chosen_tool_with_arguments(fake_llm):
    calls = []
    weather = make_tool("weather", lambda city: calls.append(city) or {"ok": True, "result": f"sunny in {city}"},
                        inputs=CITY)
    fake_llm.responses = [_use("weather", {"city": "Cupertino"})]

    report = performer_agent.run_performer("task", {"weather": weather})

    assert report == {"status": "success", "output": "sunny in Cupertino",
                      "tool_name": "weather", "arguments": {"city": "Cupertino"}}
    assert calls == ["Cupertino"]


def test_prompt_lists_tool_specs(fake_llm):
    fake_llm.responses = [_use("weather", {"city": "Cupertino"})]

    performer_agent.run_performer("task", {"weather": make_tool("weather", _ok(1), inputs=CITY)})

    _, user_prompt = fake_llm.calls[0]
    described = json.loads(user_prompt.split("Available Tools: ", 1)[1])
    assert described[0]["input_schema"]["properties"] == CITY


def test_new_tool_reports_missing_capability(fake_llm):
    fake_llm.responses = [json.dumps({"action": "new_tool", "reason": "no stock tool"})]

    report = performer_agent.run_performer("task", {})

    assert report["fault"] == "capability"
    assert report["type"] == "missing_capability"
    assert report["reason"] == "no stock tool"


def test_extend_tool_reports_extension_needed(fake_llm):
    fake_llm.responses = [json.dumps({"action": "extend_tool", "tool_name": "weather", "reason": "needs units"})]

    report = performer_agent.run_performer("task", {"weather": make_tool("weather", _ok(1), inputs=CITY)})

    assert report["fault"] == "capability"
    assert report["type"] == "extension_needed"
    assert report["tool_name"] == "weather"
    assert report["tool_source"] == "<source of weather>"


def test_reply_wrapped_in_code_fence_is_parsed(fake_llm):
    fake_llm.responses = ["```json\n" + _use("answer") + "\n```"]

    report = performer_agent.run_performer("task", {"answer": make_tool("answer", _ok("fenced"))})

    assert report["output"] == "fenced"


# --- Tool faults: reported straight to the Evolver ---

def test_crashing_tool_reports_runtime_crash(fake_llm):
    fake_llm.responses = [_use("divide")]

    report = performer_agent.run_performer("task", {"divide": make_tool("divide", lambda: 1 / 0)})

    assert report["fault"] == "tool"
    assert report["type"] == "runtime_crash"
    assert report["arguments"] == {}
    assert "ZeroDivisionError" in report["trace"]


def test_tool_reported_error_is_a_tool_fault(fake_llm):
    error = {"type": "auth", "message": "HTTP Error 401: Unauthorized"}
    fake_llm.responses = [_use("stocks")]

    report = performer_agent.run_performer("task", {"stocks": make_tool("stocks", lambda: {"ok": False, "error": error})})

    assert report["fault"] == "tool"
    assert report["type"] == "tool_reported_error"
    assert report["error"] == error
    assert len(fake_llm.calls) == 1


def test_bare_return_value_is_a_protocol_violation(fake_llm):
    fake_llm.responses = [_use("legacy")]

    report = performer_agent.run_performer("task", {"legacy": make_tool("legacy", lambda: 328)})

    assert report["fault"] == "tool"
    assert report["type"] == "protocol_violation"
    assert report["returned"] == "328"


def test_result_not_matching_output_schema_is_an_output_violation(fake_llm):
    tool = make_tool("weather", _ok({"error": "401"}), output_schema={"type": "object", "required": ["temp"]})
    fake_llm.responses = [_use("weather")]

    report = performer_agent.run_performer("task", {"weather": tool})

    assert report["fault"] == "tool"
    assert report["type"] == "output_violation"
    assert report["errors"] == ["<root>: 'temp' is a required property"]


def test_tool_needing_repair_is_not_run(fake_llm):
    ran = []
    tool = make_tool("legacy", lambda: ran.append(1), spec_errors=["Module has no SPEC dict."])
    fake_llm.responses = [_use("legacy")]

    report = performer_agent.run_performer("task", {"legacy": tool})

    assert report["fault"] == "tool"
    assert report["type"] == "spec_violation"
    assert report["spec_errors"] == ["Module has no SPEC dict."]
    assert ran == []


def test_failure_report_includes_tool_source(write_tool, fake_llm):
    code = tool_code("failing_tool", returns="{'ok': False, 'error': {'type': 'other', 'message': 'nope'}}")
    write_tool("tools/generated/failing_tool.py", code)
    fake_llm.responses = [_use("failing_tool")]

    report = performer_agent.run_performer("task", toolbox.load_agent_tools())

    assert report["tool_source"] == code


# --- Caller faults: retried by the Performer ---

def test_invalid_arguments_are_retried_with_feedback(fake_llm):
    fake_llm.responses = [_use("weather", {"town": "Cupertino"}), _use("weather", {"city": "Cupertino"})]

    report = performer_agent.run_performer("task", {"weather": make_tool("weather", _ok("sunny"), inputs=CITY)})

    assert report["status"] == "success"
    retry_prompt = fake_llm.calls[1][1]
    assert "previous attempts for this task failed" in retry_prompt
    assert "'city' is a required property" in retry_prompt


def test_repeated_invalid_arguments_escalate_to_tool_fault(fake_llm):
    fake_llm.responses = [_use("weather", {"town": "Cupertino"})] * 3

    report = performer_agent.run_performer("task", {"weather": make_tool("weather", _ok("sunny"), inputs=CITY)})

    assert len(fake_llm.calls) == 1 + performer_agent.MAX_CALL_RETRIES
    assert report["fault"] == "tool"
    assert report["type"] == "invalid_arguments"
    assert len(report["attempts"]) == 3


def test_unknown_tool_is_retried_then_succeeds(fake_llm):
    fake_llm.responses = [_use("does_not_exist"), _use("answer")]

    report = performer_agent.run_performer("task", {"answer": make_tool("answer", _ok(42))})

    assert report["output"] == 42


def test_repeated_non_json_replies_stay_caller_fault(fake_llm):
    fake_llm.responses = ["I think you should use the answer tool."] * 3

    report = performer_agent.run_performer("task", {"answer": make_tool("answer", _ok(42))})

    assert report["fault"] == "caller"
    assert report["type"] == "invalid_llm_response"
    assert len(report["attempts"]) == 3


def test_malformed_decisions_are_caller_faults(fake_llm):
    fake_llm.responses = [json.dumps({"action": "evolve_needed"}),
                          json.dumps({"action": "use_tool", "tool_name": "answer", "arguments": ["x"]}),
                          _use("answer")]

    report = performer_agent.run_performer("task", {"answer": make_tool("answer", _ok(42))})

    assert report["output"] == 42
    assert "Unknown action 'evolve_needed'" in fake_llm.calls[1][1]


def test_bad_input_is_retried_with_different_arguments(fake_llm):
    weather = make_tool("weather", lambda city: _bad_input() if city == "Cuppertino" else {"ok": True, "result": "sunny"},
                        inputs=CITY)
    fake_llm.responses = [_use("weather", {"city": "Cuppertino"}), _use("weather", {"city": "Cupertino"})]

    report = performer_agent.run_performer("task", {"weather": weather})

    assert report["output"] == "sunny"
    assert "Unknown city" in fake_llm.calls[1][1]


def test_second_bad_input_escalates_to_tool_fault(fake_llm):
    weather = make_tool("weather", lambda city: _bad_input(), inputs=CITY)
    fake_llm.responses = [_use("weather", {"city": "Cupertino"}), _use("weather", {"city": "Cupertino, CA"})]

    report = performer_agent.run_performer("task", {"weather": weather})

    assert len(fake_llm.calls) == 2
    assert report["fault"] == "tool"
    assert report["type"] == "tool_reported_error"
    assert [a["arguments"] for a in report["attempts"]] == [{"city": "Cupertino"}, {"city": "Cupertino, CA"}]
