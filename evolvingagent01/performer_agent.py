"""The Performer: picks a tool and its arguments, runs it, and reports failures.

Every failure report has a "fault": "caller" (the LLM called a tool wrongly; retried here),
"tool" (the tool is broken; the Evolver fixes it) or "capability" (the Evolver creates or extends a tool).
See docs/TOOL_PROTOCOL.md.
"""
import json
import traceback

from . import llm, toolbox

# Retries for calling mistakes, within one generation
MAX_CALL_RETRIES = 2

# Calling mistakes that, if they keep happening, suggest the tool's interface is the problem
_ESCALATE_TO_TOOL = ("invalid_arguments", "tool_reported_error")


def run_performer(task, available_tools):
    """
    Asks the LLM which tool to use and with what arguments, and runs it, retrying calling mistakes.
    Returns a success report, or a failure report whose "fault" tells the harness whether to run the Evolver.
    """
    print(f"\n[Performer] Analyzing task: '{task}'")
    print(f"[Performer] Active toolbox: {list(available_tools.keys())}")

    attempts = []
    for attempt in range(MAX_CALL_RETRIES + 1):
        report = _attempt(task, available_tools, attempts)
        if report.get("fault") != "caller":
            return report
        attempts.append({k: report[k] for k in ("type", "message", "errors", "tool_name", "arguments", "error", "reply")
                         if k in report})
        # A tool rejecting its input twice is more likely a tool problem than a calling mistake
        if report["type"] == "tool_reported_error" and sum(a["type"] == "tool_reported_error" for a in attempts) >= 2:
            return _escalate(report, attempts)
        if attempt < MAX_CALL_RETRIES:
            print(f"[Performer] Calling mistake ({report['type']}); asking again "
                  f"(retry {attempt + 1} of {MAX_CALL_RETRIES}).")

    if report["type"] in _ESCALATE_TO_TOOL:
        return _escalate(report, attempts)
    print(f"[Performer] Giving up on this generation after {len(attempts)} calling mistakes.")
    return {**report, "attempts": attempts}


def _escalate(report, attempts):
    print(f"[Performer] Repeated '{report['type']}' for '{report.get('tool_name')}'; treating it as a tool problem.")
    return {**report, "fault": "tool", "attempts": attempts}


def _attempt(task, available_tools, previous_attempts):
    """One routing call and, if a tool is chosen, one tool run. Returns a success or failure report."""
    system_prompt = (
        "You choose which tool an AI agent should call for a task. Review the task and the available tools. Each tool lists "
        "its description, the JSON Schema of its arguments (input_schema) and of its result (output_schema).\n"
        "- Prefer calling an existing general tool with the right arguments for this task.\n"
        "- If a tool is close but is missing an input or output this task needs, ask to extend it.\n"
        "- Ask for a new tool only when no tool fits.\n"
        "- A tool with \"status\": \"needs_repair\" can still be chosen; it will be repaired first.\n"
        "Your response MUST be valid JSON in exactly one of these shapes:\n"
        "{\"action\": \"use_tool\", \"tool_name\": \"string\", \"arguments\": {<matching input_schema>}}\n"
        "{\"action\": \"extend_tool\", \"tool_name\": \"string\", \"reason\": \"what input/output is missing\"}\n"
        "{\"action\": \"new_tool\", \"reason\": \"what capability is missing\"}"
    )

    user_prompt = (f"Task: {task}\n"
                   f"Available Tools: {json.dumps(toolbox.describe_tools(available_tools), indent=2, default=str)}")
    if previous_attempts:
        user_prompt += ("\n\nYour previous attempts for this task failed:\n"
                        f"{json.dumps(previous_attempts, indent=2, default=str)}\n"
                        "Correct the mistake and reply again.")

    message_text = llm.call_llm(system_prompt, user_prompt)

    def caller_mistake(error_type, message, **extra):
        print(f"[Performer] {message}")
        return {"status": "error", "fault": "caller", "type": error_type, "message": message,
                "reply": message_text, **extra}

    try:
        decision = llm.parse_json(message_text)
    except ValueError as e:
        return caller_mistake("invalid_llm_response", f"Could not parse routing decision: {e}")

    action = decision.get("action")
    if action == "new_tool":
        return {"status": "error", "fault": "capability", "type": "missing_capability",
                "message": "No existing tool can do this task.", "reason": decision.get("reason")}
    if action not in ("use_tool", "extend_tool"):
        return caller_mistake("invalid_llm_response", f"Unknown action {action!r}; expected use_tool, "
                              "extend_tool or new_tool.")

    tool_to_call = decision.get("tool_name")
    if tool_to_call not in available_tools:
        return caller_mistake("unknown_tool", f"Tool '{tool_to_call}' does not exist. "
                              f"Available tools: {list(available_tools.keys())}")
    tool = available_tools[tool_to_call]
    # Every report about a tool names it and carries its spec and code, so the Evolver can work on it in place
    about_tool = {"tool_name": tool_to_call, "spec": tool.spec, "spec_errors": tool.spec_errors,
                  "tool_source": tool.source}

    if action == "extend_tool":
        print(f"[Performer] Tool '{tool_to_call}' needs extending: {decision.get('reason')}")
        return {"status": "error", "fault": "capability", "type": "extension_needed", **about_tool,
                "message": "An existing tool is close but needs extending.", "reason": decision.get("reason")}

    arguments = decision.get("arguments", {})
    if not isinstance(arguments, dict):
        return caller_mistake("invalid_llm_response", f"'arguments' must be a JSON object, got {arguments!r}.")
    about_tool["arguments"] = arguments
    failure = {"status": "error", "fault": "tool", **about_tool}

    if tool.spec_errors:
        print(f"[Performer] Tool '{tool_to_call}' does not follow the tool spec protocol; it needs repair.")
        return {**failure, "type": "spec_violation",
                "message": "The tool does not follow the tool spec protocol, so it cannot be called safely."}

    argument_errors = toolbox.check_arguments(tool.spec, arguments)
    if argument_errors:
        return caller_mistake("invalid_arguments", f"Arguments for '{tool_to_call}' do not match its input_schema.",
                              errors=argument_errors, **about_tool)

    print(f"[Performer] Match found. Executing '{tool_to_call}' with arguments {json.dumps(arguments, default=str)}...")
    try:
        result = tool.execute(**arguments)
    except Exception as e:
        # A crash is the tool's fault; the traceback goes to the Evolver
        print(f"[Performer] Execution crashed inside tool!")
        return {**failure, "type": "runtime_crash", "message": str(e), "trace": traceback.format_exc()}

    outcome, value = toolbox.check_result(result)
    if outcome == "ok":
        output_errors = toolbox.check_output(tool.spec, value)
        if output_errors:
            print(f"[Performer] Tool result does not match its output_schema.")
            return {**failure, "type": "output_violation",
                    "message": "The tool's result does not match its output_schema.",
                    "errors": output_errors, "returned": repr(value)[:2000]}
        return {"status": "success", "output": value, "tool_name": tool_to_call, "arguments": arguments}
    if outcome == "error":
        if isinstance(value, dict) and value.get("type") == "bad_input":
            return caller_mistake("tool_reported_error", f"Tool '{tool_to_call}' rejected its input: "
                                  f"{value.get('message')}", error=value, **about_tool)
        print(f"[Performer] Tool reported an error: {value}")
        return {**failure, "type": "tool_reported_error",
                "message": "The tool ran but reported that it could not produce the answer.",
                "error": value}
    print(f"[Performer] Tool result does not follow the tool result protocol.")
    return {**failure, "type": "protocol_violation",
            "message": "The tool's return value does not follow the tool result protocol.",
            "returned": repr(result)[:2000]}
