"""Helpers for building protocol-following tools in tests."""
from evolvingagent01 import toolbox


def spec(name, inputs=None, required=None, output_schema=None, description="Test tool."):
    """A SPEC dict. inputs maps argument name -> JSON Schema; required defaults to all inputs."""
    inputs = inputs or {}
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": inputs,
                         "required": list(inputs) if required is None else required,
                         "additionalProperties": False},
        "output_schema": output_schema if output_schema is not None else {},
    }


def tool_code(name, returns="{'ok': True, 'result': 42}", inputs=None, required=None, output_schema=None, body=None):
    """Source code for a tool file. Parameters not in `required` default to None."""
    s = spec(name, inputs, required, output_schema)
    required_names = s["input_schema"]["required"]
    params = [p if p in required_names else f"{p}=None" for p in s["input_schema"]["properties"]]
    params.sort(key=lambda p: "=" in p)  # parameters with defaults go last
    body = body or f"return {returns}"
    return f"SPEC = {s!r}\n\n\ndef execute({', '.join(params)}):\n    {body}\n"


def make_tool(name, fn, inputs=None, required=None, output_schema=None, spec_errors=None):
    """An in-memory toolbox.Tool, for Performer tests that don't need a file on disk."""
    return toolbox.Tool(execute=fn, spec=spec(name, inputs, required, output_schema),
                        source=f"<source of {name}>", spec_errors=spec_errors or [])
