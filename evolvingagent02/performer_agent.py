"""The Performer: reviews an expense report, or answers a question, by following the active SOP rules.

See docs/DESIGN.md.
"""
import json

from . import llm, sop_ledger

# Retries for calling mistakes (replies that fail sop_ledger.check_decisions())
MAX_CALL_RETRIES = 2

_EXAMPLE_REPLY = {
    "reply": "Item 1 is approved. Item 3 is flagged: its receipt amount doesn't match the log.",
    "decisions": [
        {"item_id": "1", "decision": "approve", "reason": "Receipt matches the log entry.", "rules_applied": ["R1"]},
        {"item_id": "3", "decision": "flag_for_review", "reason": "Receipt is $54.00 but the log says $45.00.",
         "rules_applied": ["R1"]},
    ],
}


def run_performer(message, report, ledger):
    """
    Asks the LLM to handle the manager's message using the ledger's active rules, retrying calling mistakes.
    Returns {"status": "success", "reply", "decisions"} or {"status": "error", "message", "attempts"}.
    """
    print(f"\n[Performer] Handling: '{message}'")
    print(f"[Performer] Active rules: {[rule['id'] for rule in sop_ledger.active_rules(ledger)]}")

    system_prompt = (
        "You are a junior travel and expense assistant. You review expense reports by matching the receipts "
        "against the expense log, and you follow your Standard Operating Procedure (SOP) rules exactly.\n"
        "- When a report is attached, give exactly one decision for every item_id in it: \"approve\", "
        "\"flag_for_review\" or \"deny\". Give the reason, and list the IDs of the rules you applied.\n"
        "- When several rules apply, the strictest outcome wins: deny over flag_for_review over approve.\n"
        "- Approve an item when no rule calls for anything else.\n"
        "- When there is no report, answer the question in 'reply' and return an empty 'decisions' list.\n"
        "Reply with ONLY a JSON object in this shape:\n"
        f"{json.dumps(_EXAMPLE_REPLY, indent=2)}\n\n"
        f"Your SOP rules:\n{sop_ledger.format_rules(ledger)}"
    )

    user_prompt = f"Manager's message: {message}"
    if report is not None:
        user_prompt += f"\nExpense report: {json.dumps(report, indent=2)}"

    attempts = []
    for attempt in range(MAX_CALL_RETRIES + 1):
        prompt = user_prompt
        if attempts:
            prompt += ("\n\nYour previous replies were invalid:\n"
                       f"{json.dumps(attempts, indent=2)}\n"
                       "Correct the mistakes and reply again.")
        message_text = llm.call_llm(system_prompt, prompt)

        try:
            reply = llm.parse_json(message_text)
            errors = sop_ledger.check_decisions(reply, report, ledger)
        except ValueError as e:
            errors = [f"Could not parse the reply as a JSON object: {e}"]
        if not errors:
            return {"status": "success", "reply": reply["reply"], "decisions": reply["decisions"]}

        attempts.append({"reply": message_text, "errors": errors})
        if attempt < MAX_CALL_RETRIES:
            print(f"[Performer] Invalid reply ({errors[0]}); asking again (retry {attempt + 1} of {MAX_CALL_RETRIES}).")

    print(f"[Performer] Giving up after {len(attempts)} invalid replies.")
    return {"status": "error", "message": "The review could not be completed.", "attempts": attempts}
