import json

from evolvingagent02 import evolver_agent, sop_ledger
from replies import REPORT, expect, proposal

CASE = {"case_id": "c-1", "message": "Review this.", "report": REPORT, "reply": "All approved.",
        "decisions": [{"item_id": "2", "decision": "approve", "reason": "ok", "rules_applied": ["R1"]}]}


def _propose(fake_llm, *replies, last_case=CASE, ledger=None, previous_attempt=None):
    fake_llm.responses = list(replies)
    return evolver_agent.propose_change("Flag dinners over $50.", last_case, ledger or sop_ledger.seed_ledger(),
                                        previous_attempt)


def test_prompt_includes_feedback_last_case_and_active_rules(fake_llm):
    _propose(fake_llm, proposal(expected=expect("2", "flag_for_review")))

    system_prompt, user_prompt = fake_llm.calls[0]
    assert sop_ledger.format_rules(sop_ledger.seed_ledger()) in system_prompt
    assert "Flag dinners over $50." in user_prompt
    case_json = user_prompt.split("Last case: ", 1)[1]
    assert json.loads(case_json)["report"] == REPORT


def test_prompt_says_when_there_is_no_last_case(fake_llm):
    _propose(fake_llm, proposal(), last_case=None)

    assert "Last case: none" in fake_llm.calls[0][1]


def test_reads_each_proposal_type(fake_llm):
    add = _propose(fake_llm, proposal(expected=expect("2", "flag_for_review")))
    update = _propose(fake_llm, proposal("update", "Deny undated items.", replaces="R2"))
    none = _propose(fake_llm, json.dumps({"action": "none", "reply": "Thanks!"}))

    assert add["proposal"]["expected"] == expect("2", "flag_for_review")
    assert update["proposal"]["replaces"] == "R2"
    assert none == {"status": "success", "proposal": {"action": "none", "reply": "Thanks!"}}


def test_previous_attempt_appears_in_the_prompt(fake_llm):
    previous = {"rule_text": "Flag meals.", "mismatches": [{"item_id": "2", "expected": "flag_for_review",
                                                            "got": "approve"}]}

    _propose(fake_llm, proposal(), previous_attempt=previous)

    assert "Flag meals." in fake_llm.calls[0][1]
    assert "Revise the rule" in fake_llm.calls[0][1]


def test_retries_invalid_proposals_with_the_errors(fake_llm):
    result = _propose(fake_llm,
                      proposal("update", replaces="R9"),             # not an active rule
                      proposal(expected=expect("7", "deny")),        # not in the last case
                      proposal(expected=expect("2", "flag_for_review")))

    assert result["status"] == "success"
    assert "R9" in fake_llm.calls[1][1]
    assert "['7']" in fake_llm.calls[2][1]


def test_gives_up_after_the_retries(fake_llm):
    result = _propose(fake_llm, *["not json"] * (evolver_agent.MAX_CALL_RETRIES + 1))

    assert result["status"] == "error"
    assert len(result["attempts"]) == evolver_agent.MAX_CALL_RETRIES + 1


def test_check_proposal_rules():
    ledger = sop_ledger.seed_ledger()
    check = evolver_agent.check_proposal

    assert check(json.loads(proposal(rule_text="")), CASE, ledger) == ["'rule_text' is required for action 'add'."]
    assert "must be null for an add" in check(json.loads(proposal(replaces="R1")), CASE, ledger)[0]
    assert "no reviewed report" in check(json.loads(proposal(expected=expect("2", "deny"))), None, ledger)[0]
    assert check(json.loads(proposal(expected=expect("2", "deny") * 2)), CASE, ledger) == [
        "'expected' lists an item more than once."]
    assert "expected/0/decision" in check(json.loads(proposal(expected=expect("2", "maybe"))), CASE, ledger)[0]
