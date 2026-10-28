"""The Evolver: turns the manager's feedback into one proposed change to the SOP ledger.

It only proposes; loop_harness tests the change and saves it. See docs/DESIGN.md.
"""
import json

from jsonschema import Draft202012Validator

from . import llm, sop_ledger

# Retries for calling mistakes (replies that fail check_proposal())
MAX_CALL_RETRIES = 2

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"enum": ["add", "update", "none"]},
        "rule_text": {"type": ["string", "null"]},
        "replaces": {"type": ["string", "null"]},
        "expected": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {"item_id": {"type": "string"}, "decision": {"enum": list(sop_ledger.DECISIONS)}},
                "required": ["item_id", "decision"],
            },
        },
        "reply": {"type": "string"},
    },
    "required": ["action", "reply"],
}

_EXAMPLE_REPLY = {
    "action": "add",
    "rule_text": "If a receipt is for a dinner over $50, flag the item for HR review.",
    "replaces": None,
    "expected": [{"item_id": "3", "decision": "flag_for_review"}],
    "reply": "Understood. I'll flag dinners over $50 for HR review from now on.",
}


def check_proposal(proposal, last_case, ledger):
    """Check a proposal against its schema, the active rules and the last case. Returns a list of error messages."""
    errors = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
              for e in Draft202012Validator(PROPOSAL_SCHEMA).iter_errors(proposal)]
    if errors:
        return errors
    action = proposal["action"]
    if action == "none":
        return []

    if not (proposal.get("rule_text") or "").strip():
        errors.append(f"'rule_text' is required for action '{action}'.")
    active_ids = {rule["id"] for rule in sop_ledger.active_rules(ledger)}
    replaces = proposal.get("replaces")
    if action == "update" and replaces not in active_ids:
        errors.append(f"'replaces' must name an active rule for an update; got {replaces!r}. "
                      f"Active rules: {sorted(active_ids)}.")
    if action == "add" and replaces is not None:
        errors.append("'replaces' must be null for an add; use action 'update' to replace a rule.")

    expected = proposal.get("expected")
    if expected:
        case_ids = sop_ledger.report_item_ids(last_case["report"]) if last_case and last_case.get("report") else set()
        if not case_ids:
            errors.append("'expected' must be null: there is no reviewed report to correct.")
        else:
            item_ids = [e["item_id"] for e in expected]
            unknown = sorted(set(item_ids) - case_ids)
            if unknown:
                errors.append(f"'expected' names items {unknown} that are not in the last case's report "
                              f"{sorted(case_ids)}.")
            if len(item_ids) != len(set(item_ids)):
                errors.append("'expected' lists an item more than once.")
    return errors


def propose_change(feedback, last_case, ledger, previous_attempt=None):
    """
    Asks the LLM for one add, update or none proposal for the feedback, retrying calling mistakes.
    previous_attempt describes a rule that failed testing, so the LLM can revise it.
    Returns {"status": "success", "proposal"} or {"status": "error", "message", "attempts"}.
    """
    print(f"\n[Evolver] Learning from feedback: '{feedback}'")

    system_prompt = (
        "You maintain the Standard Operating Procedure (SOP) rules of a junior travel and expense assistant. "
        "Its manager has given feedback. Turn it into exactly one proposed change to the rules.\n"
        "- \"add\": the feedback teaches something new.\n"
        "- \"update\": the feedback changes or contradicts an existing rule; 'replaces' is that rule's ID, and "
        "'rule_text' is the complete replacement rule.\n"
        "- \"none\": praise, or something an existing rule already covers. Only 'reply' matters.\n"
        "Write rules that are general and reusable. Don't mention specific people, merchants, reports or dates, "
        "or amounts unless the amount is itself the policy (like a spending limit). Say what decision the rule "
        "leads to: approve, flag_for_review or deny.\n"
        "If the feedback corrects the last case, set 'expected' to the decisions the manager wants for the "
        "items it is about. Otherwise set 'expected' to null.\n"
        "'reply' is your short acknowledgement to the manager.\n"
        "Reply with ONLY a JSON object. Example reply:\n"
        f"{json.dumps(_EXAMPLE_REPLY, indent=2)}\n\n"
        f"Current SOP rules:\n{sop_ledger.format_rules(ledger)}"
    )

    case = ({k: last_case.get(k) for k in ("message", "report", "reply", "decisions")} if last_case else None)
    user_prompt = (f"Manager's feedback: {feedback}\n"
                   f"Last case: {json.dumps(case, indent=2) if case else 'none (nothing has been reviewed yet)'}")
    if previous_attempt:
        user_prompt += ("\n\nYou already proposed a rule for this feedback, but when the last case was reviewed "
                        "again with it, the decisions were still wrong:\n"
                        f"{json.dumps(previous_attempt, indent=2)}\n"
                        "Revise the rule so the review produces the expected decisions.")

    attempts = []
    for attempt in range(MAX_CALL_RETRIES + 1):
        prompt = user_prompt
        if attempts:
            prompt += ("\n\nYour previous replies were invalid:\n"
                       f"{json.dumps(attempts, indent=2)}\n"
                       "Correct the mistakes and reply again.")
        message_text = llm.call_llm(system_prompt, prompt)

        try:
            proposal = llm.parse_json(message_text)
            errors = check_proposal(proposal, last_case, ledger)
        except ValueError as e:
            errors = [f"Could not parse the reply as a JSON object: {e}"]
        if not errors:
            print(f"[Evolver] Proposal: {proposal['action']}"
                  + (f" '{proposal['rule_text']}'" if proposal["action"] != "none" else "")
                  + (f" (replaces {proposal['replaces']})" if proposal["action"] == "update" else ""))
            return {"status": "success", "proposal": proposal}

        attempts.append({"reply": message_text, "errors": errors})
        if attempt < MAX_CALL_RETRIES:
            print(f"[Evolver] Invalid reply ({errors[0]}); asking again (retry {attempt + 1} of {MAX_CALL_RETRIES}).")

    print(f"[Evolver] Giving up after {len(attempts)} invalid replies.")
    return {"status": "error", "message": "The feedback could not be turned into a rule.", "attempts": attempts}
