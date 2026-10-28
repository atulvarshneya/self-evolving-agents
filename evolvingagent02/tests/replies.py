"""Builders for the scripted LLM replies the tests feed to fake_llm, and a small sample report."""
import json

REPORT = {
    "report_id": "TR-1",
    "employee": "Test Employee",
    "expense_log": [
        {"item_id": "1", "date": "2026-10-12", "merchant": "Hotel", "category": "lodging", "amount": 100.0},
        {"item_id": "2", "date": "2026-10-13", "merchant": "Bistro", "category": "meal", "amount": 72.4},
    ],
    "receipts": [
        {"item_id": "1", "merchant": "Hotel", "date": "2026-10-12", "amount": 100.0, "description": "1 night"},
        {"item_id": "2", "merchant": "Bistro", "date": "2026-10-13", "amount": 72.4, "description": "dinner"},
    ],
}


def route(name):
    return json.dumps({"route": name})


def review(*decisions, reply="Done.", rules=("R1",)):
    """A Performer reply: review("approve", "flag_for_review") gives item 1 the first decision, item 2 the second."""
    return json.dumps({"reply": reply, "decisions": [
        {"item_id": str(n), "decision": d, "reason": "because", "rules_applied": list(rules)}
        for n, d in enumerate(decisions, start=1)]})


def proposal(action="add", rule_text="Flag dinners over $50.", replaces=None, expected=None, reply="Got it."):
    return json.dumps({"action": action, "rule_text": rule_text, "replaces": replaces,
                       "expected": expected, "reply": reply})


def expect(item_id, decision):
    return [{"item_id": item_id, "decision": decision}]
