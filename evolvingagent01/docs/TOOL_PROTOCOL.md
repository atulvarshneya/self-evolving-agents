# Tool protocol

This is the contract between the **Evolver**, which writes tools, and the **Performer**, which chooses and runs them. It has three parts:

1. the **spec protocol**: how a tool describes the arguments it takes and the result it returns
2. the **result protocol**: the shape every tool returns
3. the **fault model**: how each failure is attributed and who handles it

The protocol doesn't depend on any one LLM provider. The Performer gets tool calls back as plain JSON in the LLM's reply, not through a provider's built-in tool-use feature, so any model that can follow instructions and write JSON will do. All provider-specific code is in `llm.py`. Inputs and outputs are described in standard JSON Schema, checked with the `jsonschema` library (draft 2020-12).

| Where it lives | What |
|---|---|
| `toolbox.TOOL_SPEC_PROTOCOL`, `toolbox.TOOL_PROTOCOL` | The protocol text, pasted into the Evolver's prompt |
| `toolbox.validate_spec()`, `check_arguments()`, `check_result()`, `check_output()` | The checks that enforce it |
| `performer_agent.py` | Fault attribution and retries |
| `evolver_agent.py` | Create, fix and extend modes |

When you change a rule, change the protocol text and its check together.

## 1. Spec protocol: what a tool file must contain

Every tool, whether hand-written in `tools/` or generated in `tools/generated/`, defines a `SPEC` and an `execute()` that match each other:

```python
SPEC = {
    "name": "get_current_weather",            # must equal the filename stem
    "description": "Current weather for a city, looked up live.",
    "input_schema": {                         # JSON Schema for execute()'s arguments
        "type": "object",
        "properties": {
            "city":  {"type": "string", "description": "City name, e.g. 'Cupertino'"},
            "units": {"type": "string", "enum": ["fahrenheit", "celsius"]},
        },
        "required": ["city"],
        "additionalProperties": False,
    },
    "output_schema": {                        # JSON Schema for `result` only
        "type": "object",
        "properties": {"temperature": {"type": "number"}, "conditions": {"type": "string"}},
        "required": ["temperature", "conditions"],
    },
}


def execute(city, units="fahrenheit"):
    """Look up the current weather for a city."""
    ...
    return {"ok": True, "result": {"temperature": 61.2, "conditions": "Clear sky"}}
```

The loader (`validate_spec()`) checks these rules:

- `SPEC` is a dict with `name`, `description`, `input_schema` and `output_schema`.
- `name` equals the filename stem, and `description` is a non-empty string.
- Both schemas are valid JSON Schema, and `input_schema` has `"type": "object"`.
- The names in `input_schema.properties` are exactly `execute()`'s parameter names.
- `input_schema.required` lists exactly the parameters that have no default.
- `execute()` does not use `*args` or `**kwargs`.

The Evolver is also told, though the loader can't check these:
- set `"additionalProperties": False`
- use JSON types only, since arguments arrive from JSON
- take anything specific to the task as an argument instead of hardcoding it

**A tool that breaks these rules is kept, not dropped.** It's loaded with its `spec_errors` recorded and shown to the Performer as `"status": "needs_repair"`. If the Performer chooses it, the tool isn't run. Instead it's reported as a `spec_violation`, and the Evolver repairs it. This is how older tools without a `SPEC` get upgraded. A module that fails to import is still skipped with a warning.

## 2. Result protocol: what `execute()` returns

```python
{"ok": True,  "result": <answer, matching output_schema>}
{"ok": False, "error": {"type": "network" | "auth" | "bad_input" | "not_found" | "other",
                        "message": "...",
                        "details": {...}}}          # optional
```

- Expected failures (HTTP errors, missing data) are returned in the failure shape rather than raised.
- A failure is never put inside a success result.
- `bad_input` means the **arguments passed in** were wrong, for example an unknown city. It is not for failures inside the tool. This matters because `bad_input` is treated as the caller's fault (see §4).
- `output_schema` describes only `result`. The `{ok, ...}` wrapper around it is the same for every tool.

## 3. Routing: what the Performer sends and expects

The Performer's prompt lists every tool from `toolbox.describe_tools()`: `{name, description, input_schema, output_schema}`, or `{name, status: "needs_repair", description}` for a tool with spec errors. The LLM replies with exactly one of these:

```json
{"action": "use_tool",    "tool_name": "...", "arguments": {...}}
{"action": "extend_tool", "tool_name": "...", "reason": "what input/output is missing"}
{"action": "new_tool",    "reason": "what capability is missing"}
```

The LLM is told to prefer calling a general tool with arguments, to ask for an extension when a tool is close but missing something, and to ask for a new tool only when nothing fits. If `arguments` is missing it defaults to `{}`; if present, it must be a JSON object.

## 4. Fault model

Every failure report has a `fault` field, and that decides what happens next:

| Situation | `type` | `fault` | What happens |
|---|---|---|---|
| Reply isn't JSON, has an unknown `action`, or `arguments` isn't an object | `invalid_llm_response` | caller | Performer retries |
| `tool_name` doesn't exist | `unknown_tool` | caller | Performer retries |
| Arguments don't match `input_schema` | `invalid_arguments` | caller | Performer retries |
| Tool returns `ok: False` with error type `bad_input` | `tool_reported_error` | caller | Performer retries; a second `bad_input` makes it a tool fault |
| Chosen tool has spec errors (§1) | `spec_violation` | tool | Evolver fixes |
| `execute()` raises | `runtime_crash` | tool | Evolver fixes |
| `ok: False`, any other error type | `tool_reported_error` | tool | Evolver fixes |
| Return value isn't either result shape | `protocol_violation` | tool | Evolver fixes |
| `result` doesn't match `output_schema` | `output_violation` | tool | Evolver fixes |
| `new_tool` | `missing_capability` | capability | Evolver creates |
| `extend_tool` | `extension_needed` | capability | Evolver extends |

### Retries

When the caller is at fault, the Performer asks the LLM again. It does this up to `MAX_CALL_RETRIES = 2` times per generation, and retries don't use up a generation. Each retry prompt includes all previous failed attempts: the reply, the `type`, the `message`, any validation `errors`, and the `arguments`.

If the retries run out:
- **`invalid_arguments` or `tool_reported_error` becomes a tool fault.** If a tool keeps being called wrongly, its interface is probably unclear, so it goes to the Evolver. The report includes every attempt in `attempts`.
- **`invalid_llm_response` or `unknown_tool` stays a caller fault.** There's no tool to fix, so the loop moves to the next generation without running the Evolver.

### What a report contains

Every report about a specific tool includes `tool_name`, `spec`, `spec_errors`, `tool_source`, and `arguments` (once arguments have been read). A success report is `{"status": "success", "output", "tool_name", "arguments"}`.

## 5. Evolver modes

The Evolver gets both protocol texts, an example tool that passes the loader's checks, and a mode chosen from the report `type`:

| Mode | Triggered by | Rules |
|---|---|---|
| **Create** | `missing_capability` | Write a general tool: anything specific to the task becomes an argument. |
| **Fix** | any tool fault | Keep `input_schema` unchanged (argument names, types, which are required), so existing calls keep working. Descriptions may be clarified. Exception: for a `spec_violation`, the spec may be changed as much as needed to follow §1. |
| **Extend** | `extension_needed` | Keep all existing parameters. Add new ones only as optional parameters with defaults. |

For a fix or an extension, the prompt shows the tool's current code, and the result is always saved as `<tool_name>.py`, even if the LLM suggests another name, so it replaces the old version. The Evolver doesn't check its own output. The loader checks every tool at the start of the next generation, and an invalid result goes through the `needs_repair` path above.

## 6. The loop

Each generation (`loop_harness.main()`):
1. Load the tools.
2. Run the Performer, including its retries.
3. Then, depending on the outcome:
   - **Success:** print the result and stop.
   - **Tool fault or capability fault:** run the Evolver.
   - **Caller fault:** go straight to the next generation.

There are at most 3 Evolver runs, followed by one last Performer pass so the last tool written still gets tried.
