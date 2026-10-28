"""The Router: decides whether a manager's message is a task to perform or feedback to learn from."""
from . import llm

# Retries for unparseable replies
MAX_CALL_RETRIES = 2


def route_message(message, has_report):
    """Return "task" or "feedback". A message with a report is always a task; anything unclear is treated as one."""
    if has_report:
        print("[Router] A report is attached: treating the message as a task.")
        return "task"

    system_prompt = (
        "You sort messages that a manager sends to a junior expense assistant.\n"
        "- \"task\": a request or a question, e.g. \"What's our policy on dinners?\"\n"
        "- \"feedback\": a correction of the assistant's last review, a new instruction, or a policy statement, "
        "e.g. \"That dinner should have been flagged\" or \"From now on, deny alcohol\".\n"
        "Reply with ONLY a JSON object: {\"route\": \"task\"} or {\"route\": \"feedback\"}"
    )

    for attempt in range(MAX_CALL_RETRIES + 1):
        message_text = llm.call_llm(system_prompt, f"Message: {message}")
        try:
            route = llm.parse_json(message_text).get("route")
        except ValueError:
            route = None
        if route in ("task", "feedback"):
            print(f"[Router] Message is {'a task' if route == 'task' else 'feedback'}.")
            return route
        if attempt < MAX_CALL_RETRIES:
            print(f"[Router] Could not read the route from {message_text!r}; asking again "
                  f"(retry {attempt + 1} of {MAX_CALL_RETRIES}).")

    # A task never changes the ledger, so it's the safe default
    print("[Router] Still no valid route; treating the message as a task.")
    return "task"
