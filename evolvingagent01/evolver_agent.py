"""The Evolver: asks the LLM to create a new tool, or fix or extend an existing one, and saves it.

See docs/TOOL_PROTOCOL.md.
"""
import json

from . import llm, toolbox

_EXAMPLE_CODE = '''SPEC = {
    "name": "add_numbers",
    "description": "Add two numbers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "First number"},
            "b": {"type": "number", "description": "Second number"},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    },
    "output_schema": {"type": "number"},
}


def execute(a, b):
    """Add two numbers and return their sum."""
    return {"ok": True, "result": a + b}
'''

_MODE_INSTRUCTIONS = {
    "create": ("MODE: CREATE a new tool. Make it general: take everything specific to this task as arguments "
               "so the tool can be reused for similar tasks."),
    "fix": ("MODE: FIX the existing tool shown below so it no longer fails this way. Return its same filename. "
            "Keep its input_schema (argument names, types, required) unchanged so existing calls keep working; "
            "you may clarify descriptions. Only if the failure is a spec_violation may you change the spec, "
            "and then only as much as needed to follow the tool spec protocol."),
    "extend": ("MODE: EXTEND the existing tool shown below with what this task needs (see 'reason'). Return its "
               "same filename. Keep all existing parameters; add new ones only as optional parameters with "
               "defaults, so calls written for the old version keep working."),
}


def _mode(failure_context):
    if failure_context["type"] == "missing_capability":
        return "create"
    if failure_context["type"] == "extension_needed":
        return "extend"
    return "fix"


def run_evolver(task, failure_context):
    """
    Called for the Performer's tool and capability faults. Asks the LLM to create, fix or extend
    a tool and saves it to tools/generated/. Returns the saved path, or None if nothing was written.
    """
    mode = _mode(failure_context)
    print(f"\n[Evolver] Modifying codebase to resolve: {failure_context['type']} (mode: {mode})")

    system_prompt = (
        "You are a software engineer who writes tools for an AI agent. Each tool is one self-contained Python module "
        "that the agent loads and calls. Write clean, production-grade code.\n"
        "Reply with ONLY a JSON object with two keys: 'filename' (snake_case, ending in .py) and 'code' "
        "(the complete source code of the module).\n\n"
        "RULES:\n"
        "1. The code MUST follow the tool spec protocol and the tool result protocol below.\n"
        "2. execute() MUST have a docstring describing what it does.\n"
        "3. Use the Python standard library only. There is no way to supply API keys or other credentials, "
        "so use data sources that do not need them.\n"
        "4. Do not write any text before or after the JSON object.\n\n"
        f"{toolbox.TOOL_SPEC_PROTOCOL}\n\n"
        f"{toolbox.TOOL_PROTOCOL}\n\n"
        "Example reply:\n"
        f"{json.dumps({'filename': 'add_numbers.py', 'code': _EXAMPLE_CODE})}"
    )

    tool_name = failure_context.get("tool_name")
    diagnostic = {k: v for k, v in failure_context.items() if k != "tool_source"}
    user_prompt = (f"Task: {task}\n"
                   f"{_MODE_INSTRUCTIONS[mode]}\n"
                   f"Failure report: {json.dumps(diagnostic, default=str)}")
    if tool_name:
        user_prompt += (f"\n\nExisting tool: '{tool_name}' (filename: {tool_name}.py)\n"
                        f"Current code:\n```python\n{failure_context.get('tool_source') or '<source unavailable>'}\n```")

    message_text = llm.call_llm(system_prompt, user_prompt)

    try:
        evolution_payload = llm.parse_json(message_text)
        filename = evolution_payload["filename"]
        code_content = evolution_payload["code"]
    except (ValueError, KeyError) as e:
        print(f"[Evolver] Could not parse evolution payload, nothing written: {e!r}")
        return None

    # A fix or extension must replace the existing tool, or the old version would stay in the toolbox
    if tool_name and filename not in (tool_name, f"{tool_name}.py"):
        print(f"[Evolver] Tool was named '{filename}'; writing it as '{tool_name}.py' to replace the existing tool.")
        filename = f"{tool_name}.py"

    # Save to tools/generated/; the loader checks the new tool next generation
    file_path = toolbox.save_tool(filename, code_content)
    if file_path:
        print(f"[Evolver] Mutated file successfully saved to '{file_path}'.")
    return file_path
