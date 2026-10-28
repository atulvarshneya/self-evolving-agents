"""What a tool is, where tools live, and the protocols tools must follow.

See docs/TOOL_PROTOCOL.md for the full protocol.
"""
import os
import sys
import importlib
import importlib.util
import inspect
import time
from dataclasses import dataclass, field

import jsonschema

# The contract between Evolver-written tools and the Performer that runs them.
# TOOL_SPEC_PROTOCOL and TOOL_PROTOCOL are what the Evolver is told; validate_spec() (run by the loader)
# and check_arguments(), check_result() and check_output() (run by the Performer) enforce them.
# Change each text and its checks together.
TOOL_SPEC_PROTOCOL = (
    "TOOL SPEC PROTOCOL (mandatory):\n"
    "Every tool module MUST define a top-level SPEC dict and an execute() function that match each other:\n"
    "  SPEC = {\n"
    "      \"name\": \"<must equal the filename without .py>\",\n"
    "      \"description\": \"<what the tool does, for choosing it>\",\n"
    "      \"input_schema\": {<JSON Schema with \"type\": \"object\" describing execute()'s arguments>},\n"
    "      \"output_schema\": {<JSON Schema describing the value of \"result\" on success>},\n"
    "  }\n"
    "- input_schema \"properties\" MUST list exactly execute()'s parameter names, with a type and description each.\n"
    "- input_schema \"required\" MUST list exactly the parameters that have no default value.\n"
    "- Set \"additionalProperties\": False in input_schema.\n"
    "- execute() MUST NOT use *args or **kwargs. Arguments arrive from JSON, so use JSON types only.\n"
    "- Take anything task-specific (places, names, numbers, limits, symbols) as an argument; never hardcode it."
)

TOOL_PROTOCOL = (
    "TOOL RESULT PROTOCOL (mandatory):\n"
    "execute() MUST return a dict in exactly one of these two shapes:\n"
    "  Success: {\"ok\": True, \"result\": <the answer, JSON-serializable, matching output_schema>}\n"
    "  Failure: {\"ok\": False, \"error\": {\"type\": \"network\"|\"auth\"|\"bad_input\"|\"not_found\"|\"other\", "
    "\"message\": \"<what went wrong>\", \"details\": {<optional extra info, e.g. status code, URL>}}}\n"
    "Use error type \"bad_input\" ONLY when the arguments passed in are wrong (e.g. an unknown city), "
    "not for failures inside the tool.\n"
    "Report expected failures (HTTP errors, missing data, bad responses) with the Failure shape instead of raising.\n"
    "NEVER put an error inside a Success result: if the answer could not be produced, ok MUST be False."
)

_SPEC_KEYS = ("name", "description", "input_schema", "output_schema")


@dataclass
class Tool:
    """A loaded tool. spec_errors is empty when the tool follows TOOL_SPEC_PROTOCOL."""
    execute: object
    spec: object
    source: str = None
    spec_errors: list = field(default_factory=list)


def validate_spec(name, module):
    """Check a tool module against TOOL_SPEC_PROTOCOL. Returns a list of problems (empty if valid)."""
    spec = getattr(module, "SPEC", None)
    if not isinstance(spec, dict):
        return ["Module has no SPEC dict."]

    errors = [f"SPEC is missing '{key}'." for key in _SPEC_KEYS if key not in spec]
    if errors:
        return errors
    if spec["name"] != name:
        errors.append(f"SPEC name '{spec['name']}' does not match the filename '{name}'.")
    if not isinstance(spec["description"], str) or not spec["description"].strip():
        errors.append("SPEC description must be a non-empty string.")
    for key in ("input_schema", "output_schema"):
        try:
            jsonschema.Draft202012Validator.check_schema(spec[key])
        except jsonschema.SchemaError as e:
            errors.append(f"SPEC {key} is not valid JSON Schema: {e.message}")
    if errors:
        return errors

    input_schema = spec["input_schema"]
    if input_schema.get("type") != "object":
        return errors + ["SPEC input_schema must have \"type\": \"object\"."]

    try:
        params = inspect.signature(module.execute).parameters.values()
    except (TypeError, ValueError):
        return errors + ["execute is not a function with a readable signature."]
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        errors.append("execute() must not use *args or **kwargs.")
    param_names = {p.name for p in params}
    required_params = {p.name for p in params if p.default is p.empty}
    properties = set(input_schema.get("properties", {}))
    required = set(input_schema.get("required", []))
    if properties != param_names:
        errors.append(f"input_schema properties {sorted(properties)} do not match "
                      f"execute() parameters {sorted(param_names)}.")
    if required != required_params:
        errors.append(f"input_schema required {sorted(required)} does not match "
                      f"execute() parameters without defaults {sorted(required_params)}.")
    return errors


def _schema_errors(schema, instance):
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in validator.iter_errors(instance)]


def check_arguments(spec, arguments):
    """Check call arguments against the tool's input_schema. Returns a list of problems."""
    return _schema_errors(spec["input_schema"], arguments)


def check_output(spec, result):
    """Check a successful tool's result against its output_schema. Returns a list of problems."""
    return _schema_errors(spec["output_schema"], result)


def check_result(result):
    """Classify a tool's return value against TOOL_PROTOCOL.

    Returns ("ok", <result>), ("error", <error>) or ("violation", None).
    """
    if isinstance(result, dict) and result.get("ok") is True and "result" in result:
        return "ok", result["result"]
    if isinstance(result, dict) and result.get("ok") is False:
        return "error", result.get("error")
    return "violation", None


def describe_tools(tools):
    """The tool list shown to the Performer's LLM: each tool's SPEC, or a needs_repair marker."""
    described = []
    for name, tool in tools.items():
        if tool.spec_errors:
            description = tool.spec.get("description") if isinstance(tool.spec, dict) else None
            described.append({"name": name, "status": "needs_repair", "description": description})
        else:
            described.append({key: tool.spec[key] for key in _SPEC_KEYS})
    return described


# Tools live next to this file, so the agent works whatever the current directory is.
# Tests point TOOLS_DIR at a temporary folder.
TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def _generated_dir():
    return os.path.join(TOOLS_DIR, "generated")


def setup_environment():
    """Create the tools folders if missing, and delete generated tools more than a day old."""
    os.makedirs(_generated_dir(), exist_ok=True)
    init_file = os.path.join(TOOLS_DIR, "__init__.py")
    if not os.path.exists(init_file):
        with open(init_file, "w") as f:
            f.write("")

    # Clean up generated tools that are more than 1 day old
    cutoff = time.time() - 24 * 60 * 60
    for entry in os.scandir(_generated_dir()):
        if entry.is_file() and entry.name.endswith(".py") and entry.stat().st_mtime < cutoff:
            os.remove(entry.path)


def _make_tool(name, module):
    # Tools that break the spec protocol are kept (flagged), so the Evolver can repair them when chosen
    return Tool(execute=module.execute, spec=getattr(module, "SPEC", None),
                source=_module_source(module), spec_errors=validate_spec(name, module))


def _import_tool(module_name, path):
    """Import a tool from its file path. Runs the file fresh every time, so rewritten tools are picked up."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered like a normal import, since some code (e.g. dataclasses) looks its module up here
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def load_agent_tools():
    """Load every tool in tools/ and tools/generated/. Returns {name: Tool}; files that fail to import are skipped."""
    tools = {}
    setup_environment()
    # Generated tools come second, so a generated fix replaces a hand-written tool of the same name
    for folder, package in ((TOOLS_DIR, f"{__package__}.tools"), (_generated_dir(), f"{__package__}.tools.generated")):
        for file in sorted(os.listdir(folder)):
            if file.endswith(".py") and file != "__init__.py":
                try:
                    module = _import_tool(f"{package}.{file[:-3]}", os.path.join(folder, file))
                    if hasattr(module, "execute"):
                        tools[file[:-3]] = _make_tool(file[:-3], module)
                except Exception as e:
                    print(f"[System Warning] Failed to dynamically load tool '{file}': {e}")

    return tools


def _module_source(module):
    """Return a tool module's source code, or None if it can't be read."""
    try:
        return inspect.getsource(module)
    except (TypeError, OSError):
        return None


def save_tool(filename, code):
    """Write a tool to tools/generated/. Returns the file path, or None if the filename is rejected."""
    # Only accept a bare module name, so the file lands in tools/generated/ and is importable
    if not filename.endswith(".py"):
        filename += ".py"
    if not filename[:-3].isidentifier() or filename == "__init__.py":
        print(f"[Toolbox] Rejected unsafe or non-importable filename '{filename}', nothing written.")
        return None

    file_path = os.path.join(_generated_dir(), filename)
    with open(file_path, "w") as f:
        f.write(code)
    return file_path
