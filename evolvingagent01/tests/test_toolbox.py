import os
import time

from evolvingagent01 import toolbox
from tool_code import spec, tool_code


def test_loads_tools_from_both_folders(write_tool):
    write_tool("tools/hand_written.py", "def execute():\n    return 'hand'")
    write_tool("tools/generated/generated_one.py", "def execute():\n    return 'gen'")

    tools = toolbox.load_agent_tools()

    assert set(tools) == {"hand_written", "generated_one"}
    assert tools["hand_written"].execute() == "hand"
    assert tools["generated_one"].execute() == "gen"


def test_skips_files_without_execute_and_init(write_tool):
    write_tool("tools/generated/__init__.py", "")
    write_tool("tools/generated/no_execute.py", "def run():\n    return 1")
    write_tool("tools/generated/notes.txt", "def execute():\n    return 1")

    assert toolbox.load_agent_tools() == {}


def test_broken_tool_warns_and_others_still_load(write_tool, capsys):
    write_tool("tools/generated/broken.py", "def execute(:\n    return 1")
    write_tool("tools/generated/good.py", "def execute():\n    return 'ok'")

    tools = toolbox.load_agent_tools()

    assert set(tools) == {"good"}
    assert "[System Warning] Failed to dynamically load tool 'broken.py'" in capsys.readouterr().out


def test_reload_picks_up_rewritten_tool(write_tool):
    write_tool("tools/generated/changing.py", "def execute():\n    return 1")
    assert toolbox.load_agent_tools()["changing"].execute() == 1

    # Different length on purpose: cached bytecode is validated by source mtime (whole seconds)
    # and size, so a same-size rewrite within the same second could be served stale.
    write_tool("tools/generated/changing.py", "def execute():\n    return 'rewritten'")
    assert toolbox.load_agent_tools()["changing"].execute() == "rewritten"


TWO_DAYS_AGO = time.time() - 2 * 24 * 60 * 60


def _make(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def execute():\n    return 1")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_creates_folders_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(toolbox, "TOOLS_DIR", str(tmp_path / "tools"))

    toolbox.setup_environment()

    assert (tmp_path / "tools").is_dir()
    assert (tmp_path / "tools/generated").is_dir()
    assert (tmp_path / "tools/__init__.py").read_text() == ""


def test_tools_dir_is_inside_the_package():
    package_dir = os.path.dirname(os.path.abspath(toolbox.__file__))

    assert toolbox.TOOLS_DIR == os.path.join(package_dir, "tools")


def test_deletes_only_old_generated_python_files(tmp_path, monkeypatch):
    monkeypatch.setattr(toolbox, "TOOLS_DIR", str(tmp_path / "tools"))
    old_generated = _make(tmp_path / "tools/generated/old_tool.py", TWO_DAYS_AGO)
    new_generated = _make(tmp_path / "tools/generated/new_tool.py")
    old_non_python = _make(tmp_path / "tools/generated/old_notes.txt", TWO_DAYS_AGO)
    old_hand_written = _make(tmp_path / "tools/hand_tool.py", TWO_DAYS_AGO)

    toolbox.setup_environment()

    assert not old_generated.exists()
    assert new_generated.exists()
    assert old_non_python.exists()
    assert old_hand_written.exists()


# --- Saving tools ---

def _all_files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def test_path_traversal_filename_is_rejected(agent_workdir):
    before = _all_files(agent_workdir)

    assert toolbox.save_tool("../escape.py", "def execute():\n    return 1") is None
    assert toolbox.save_tool("nested/inner.py", "def execute():\n    return 1") is None

    assert _all_files(agent_workdir) == before
    assert not (agent_workdir.parent / "escape.py").exists()


def test_filename_without_py_extension_is_still_loadable(agent_workdir):
    path = toolbox.save_tool("answer", "def execute():\n    return 42")

    assert path.endswith("answer.py")
    assert toolbox.load_agent_tools()["answer"].execute() == 42


# --- Checking results against the protocol ---

def test_check_result_classifies_return_values():
    assert toolbox.check_result({"ok": True, "result": 0}) == ("ok", 0)
    assert toolbox.check_result({"ok": False, "error": {"type": "auth"}}) == ("error", {"type": "auth"})
    assert toolbox.check_result({"ok": True}) == ("violation", None)
    assert toolbox.check_result({"ok": "yes", "result": 1}) == ("violation", None)
    assert toolbox.check_result(328) == ("violation", None)


# --- Tool spec protocol ---

WEATHER_INPUTS = {"city": {"type": "string"}, "units": {"type": "string", "enum": ["c", "f"]}}


def _spec_errors(write_tool, name, code):
    write_tool(f"tools/generated/{name}.py", code)
    return toolbox.load_agent_tools()[name].spec_errors


def test_valid_tool_has_no_spec_errors(write_tool):
    code = tool_code("weather", inputs=WEATHER_INPUTS, required=["city"])

    assert _spec_errors(write_tool, "weather", code) == []


def test_tool_without_spec_is_kept_but_flagged(write_tool):
    write_tool("tools/generated/legacy.py", "def execute():\n    return 328")

    tool = toolbox.load_agent_tools()["legacy"]

    assert tool.spec is None
    assert tool.spec_errors == ["Module has no SPEC dict."]
    assert tool.source.strip() == "def execute():\n    return 328"


def test_spec_name_must_match_filename(write_tool):
    errors = _spec_errors(write_tool, "weather", tool_code("forecast"))

    assert any("does not match the filename" in e for e in errors)


def test_spec_properties_must_match_parameters(write_tool):
    code = tool_code("weather", inputs=WEATHER_INPUTS).replace("def execute(city, units)", "def execute(city, days)")

    errors = _spec_errors(write_tool, "weather", code)

    assert any("do not match execute() parameters" in e for e in errors)


def test_spec_required_must_match_parameters_without_defaults(write_tool):
    # Both parameters are required in the schema, but units has a default
    code = tool_code("weather", inputs=WEATHER_INPUTS).replace("def execute(city, units)", "def execute(city, units='f')")

    errors = _spec_errors(write_tool, "weather", code)

    assert any("parameters without defaults" in e for e in errors)


def test_execute_must_not_take_kwargs(write_tool):
    code = tool_code("weather").replace("def execute()", "def execute(**kwargs)")

    errors = _spec_errors(write_tool, "weather", code)

    assert "execute() must not use *args or **kwargs." in errors


def test_schemas_must_be_valid_json_schema(write_tool):
    code = tool_code("weather", output_schema={"type": "not-a-type"})

    errors = _spec_errors(write_tool, "weather", code)

    assert any("output_schema is not valid JSON Schema" in e for e in errors)


def test_input_schema_must_be_an_object(write_tool):
    code = tool_code("weather").replace("'type': 'object'", "'type': 'array'")

    errors = _spec_errors(write_tool, "weather", code)

    assert errors == ['SPEC input_schema must have "type": "object".']


def test_check_arguments():
    s = spec("weather", WEATHER_INPUTS, required=["city"])

    assert toolbox.check_arguments(s, {"city": "Cupertino"}) == []
    assert toolbox.check_arguments(s, {}) == ["<root>: 'city' is a required property"]
    assert toolbox.check_arguments(s, {"city": 5}) == ["city: 5 is not of type 'string'"]
    assert toolbox.check_arguments(s, {"city": "X", "units": "k"}) == ["units: 'k' is not one of ['c', 'f']"]
    assert len(toolbox.check_arguments(s, {"city": "X", "zip": "95014"})) == 1


def test_check_output():
    s = spec("weather", output_schema={"type": "object", "required": ["temp"]})

    assert toolbox.check_output(s, {"temp": 61}) == []
    assert toolbox.check_output(s, {"error": "401"}) == ["<root>: 'temp' is a required property"]


def test_describe_tools_shows_specs_and_needs_repair(write_tool):
    write_tool("tools/generated/weather.py", tool_code("weather", inputs=WEATHER_INPUTS))
    write_tool("tools/generated/legacy.py", "def execute():\n    return 328")

    described = {d["name"]: d for d in toolbox.describe_tools(toolbox.load_agent_tools())}

    assert described["weather"]["input_schema"]["properties"] == WEATHER_INPUTS
    assert set(described["weather"]) == {"name", "description", "input_schema", "output_schema"}
    assert described["legacy"] == {"name": "legacy", "status": "needs_repair", "description": None}
