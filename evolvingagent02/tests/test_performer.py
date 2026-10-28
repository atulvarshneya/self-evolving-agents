import json

from evolvingagent02 import performer_agent, sop_ledger
from replies import REPORT, review


def test_successful_review(fake_llm):
    fake_llm.responses = [review("approve", "deny", reply="One denied.")]

    result = performer_agent.run_performer("Review this.", REPORT, sop_ledger.seed_ledger())

    assert result["status"] == "success"
    assert result["reply"] == "One denied."
    assert [d["decision"] for d in result["decisions"]] == ["approve", "deny"]


def test_prompt_includes_active_rules_message_and_report(fake_llm):
    ledger = sop_ledger.apply_change(sop_ledger.seed_ledger(),
                                     {"action": "update", "rule_text": "Newer rule.", "replaces": "R2"})
    fake_llm.responses = [review("approve", "approve")]

    performer_agent.run_performer("Review this.", REPORT, ledger)

    system_prompt, user_prompt = fake_llm.calls[0]
    assert "R3: Newer rule." in system_prompt
    assert sop_ledger.SEED_RULES[1] not in system_prompt  # superseded rules are hidden
    assert "Review this." in user_prompt
    assert json.loads(user_prompt.split("Expense report: ", 1)[1]) == REPORT


def test_question_without_report(fake_llm):
    fake_llm.responses = [json.dumps({"reply": "Dinners need a receipt.", "decisions": []})]

    result = performer_agent.run_performer("What's our dinner policy?", None, sop_ledger.seed_ledger())

    assert result == {"status": "success", "reply": "Dinners need a receipt.", "decisions": []}
    assert "Expense report" not in fake_llm.calls[0][1]


def test_retries_schema_item_and_rule_mistakes_with_the_errors(fake_llm):
    fake_llm.responses = [
        "not json",
        review("approve"),                             # item 2 is missing
        review("approve", "approve", rules=["R9"]),    # R9 doesn't exist
    ]

    result = performer_agent.run_performer("Review this.", REPORT, sop_ledger.seed_ledger())

    assert result["status"] == "error"
    assert len(result["attempts"]) == performer_agent.MAX_CALL_RETRIES + 1
    retry_prompt = fake_llm.calls[2][1]
    assert "Could not parse" in retry_prompt and "exactly the report's items" in retry_prompt


def test_recovers_after_a_mistake(fake_llm):
    fake_llm.responses = [review("approve"), review("approve", "flag_for_review")]

    result = performer_agent.run_performer("Review this.", REPORT, sop_ledger.seed_ledger())

    assert result["status"] == "success"
    assert len(fake_llm.calls) == 2
