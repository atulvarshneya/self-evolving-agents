"""The SOP ledger: rule records, the seed rules, the last case, and the checks on a Performer's decisions.

The LLM never edits the ledger directly. It proposes one change, and apply_change() makes it.
See docs/DESIGN.md.
"""
import copy
import json
import os
from datetime import datetime

from jsonschema import Draft202012Validator

# Located relative to this package, so memory doesn't depend on the current folder. Tests point it elsewhere.
MEMORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")

SEED_RULES = [
    "Match each receipt amount to its expense log entry. Flag the item for review if the amounts differ.",
    "Deny any expense that lacks a valid merchant name or date.",
]

DECISIONS = ("approve", "flag_for_review", "deny")

DECISIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "decision": {"enum": list(DECISIONS)},
                    "reason": {"type": "string"},
                    "rules_applied": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["item_id", "decision", "reason", "rules_applied"],
            },
        },
    },
    "required": ["reply", "decisions"],
}


def _ledger_path():
    return os.path.join(MEMORY_DIR, "sop_ledger.json")


def _last_case_path():
    return os.path.join(MEMORY_DIR, "last_case.json")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _new_rule(rule_id, text, source, supersedes=None, example=None, verified=False):
    return {"id": rule_id, "text": text, "status": "active", "source": source, "created_at": _now(),
            "supersedes": supersedes, "superseded_by": None, "example": example, "verified": verified}


def seed_ledger():
    """A fresh ledger holding only the seed rules."""
    return [_new_rule(f"R{n}", text, "seed") for n, text in enumerate(SEED_RULES, start=1)]


def load_ledger():
    """Read the ledger from disk, creating it from the seed rules on first use."""
    if not os.path.exists(_ledger_path()):
        save_ledger(seed_ledger())
    with open(_ledger_path()) as f:
        return json.load(f)


def save_ledger(ledger):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(_ledger_path(), "w") as f:
        json.dump(ledger, f, indent=2)


def _next_id(ledger):
    # Rules are never deleted, only superseded, so max + 1 never reuses an ID
    return f"R{max((int(rule['id'][1:]) for rule in ledger), default=0) + 1}"


def apply_change(ledger, proposal, example=None):
    """Return a candidate ledger with an "add" or "update" proposal applied; the given ledger is left unchanged.

    The new rule is always the candidate's last record, saved with "verified": False until the harness tests it.
    """
    candidate = copy.deepcopy(ledger)
    new_id = _next_id(candidate)
    replaces = proposal.get("replaces") if proposal["action"] == "update" else None
    if replaces:
        old_rule = next(rule for rule in candidate if rule["id"] == replaces)
        old_rule["status"] = "superseded"
        old_rule["superseded_by"] = new_id
    candidate.append(_new_rule(new_id, proposal["rule_text"], "feedback", supersedes=replaces, example=example))
    return candidate


def active_rules(ledger):
    return [rule for rule in ledger if rule["status"] == "active"]


def format_rules(ledger):
    """The active rules as 'R<n>: <text>' lines, for the LLM's prompts."""
    return "\n".join(f"{rule['id']}: {rule['text']}" for rule in active_rules(ledger))


def report_item_ids(report):
    """Every item ID in a report, from its expense log and its receipts."""
    return {entry["item_id"] for key in ("expense_log", "receipts") for entry in report.get(key, [])}


def check_decisions(reply, report, ledger):
    """Check a Performer reply against the decisions schema, the report's items and the active rules.

    Returns a list of error messages; empty means the reply is valid.
    """
    errors = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
              for e in Draft202012Validator(DECISIONS_SCHEMA).iter_errors(reply)]
    if errors:
        return errors

    decided = [d["item_id"] for d in reply["decisions"]]
    expected_ids = report_item_ids(report) if report else set()
    if len(decided) != len(set(decided)):
        errors.append(f"Each item must have exactly one decision; got item_ids {decided}.")
    if set(decided) != expected_ids:
        errors.append(f"Decisions must cover exactly the report's items {sorted(expected_ids)}; "
                      f"got {sorted(set(decided))}.")

    active_ids = {rule["id"] for rule in active_rules(ledger)}
    for d in reply["decisions"]:
        unknown = [rule_id for rule_id in d["rules_applied"] if rule_id not in active_ids]
        if unknown:
            errors.append(f"Item {d['item_id']} cites {unknown}, which are not active rules. "
                          f"Active rules: {sorted(active_ids)}.")
    return errors


def new_case_id():
    return "c-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def load_last_case():
    """The most recent reviewed task ({case_id, message, report, reply, decisions}), or None."""
    if not os.path.exists(_last_case_path()):
        return None
    with open(_last_case_path()) as f:
        return json.load(f)


def save_last_case(case):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(_last_case_path(), "w") as f:
        json.dump(case, f, indent=2)


def clear_last_case():
    if os.path.exists(_last_case_path()):
        os.remove(_last_case_path())
