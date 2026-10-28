import json

import pytest

from evolvingagent02 import loop_harness, sop_ledger
from replies import REPORT, expect, proposal, review, route

FLAG_DINNER = expect("2", "flag_for_review")


@pytest.fixture
def reviewed(fake_llm):
    """A first review of REPORT, approving both items, saved as the last case."""
    fake_llm.responses = [review("approve", "approve")]
    loop_harness.chat("Review this.", REPORT)
    return fake_llm


def test_task_is_performed_and_saved_as_the_last_case(fake_llm):
    fake_llm.responses = [review("approve", "deny")]

    result = loop_harness.chat("Review this.", REPORT)

    assert result["route"] == "task" and result["status"] == "success"
    case = sop_ledger.load_last_case()
    assert case["case_id"] == result["case_id"]
    assert (case["message"], case["report"], case["decisions"]) == ("Review this.", REPORT, result["decisions"])


def test_failed_task_keeps_the_previous_last_case(reviewed):
    reviewed.responses = ["not json"] * 3

    result = loop_harness.chat("Review again.", REPORT)

    assert result["status"] == "error"
    assert sop_ledger.load_last_case()["message"] == "Review this."


def test_demo_script(reviewed):
    # The manager corrects the dinner; the rule is tested on the last case and saved
    reviewed.responses = [route("feedback"), proposal(expected=FLAG_DINNER),
                          review("approve", "flag_for_review", rules=["R1", "R3"])]
    learned = loop_harness.chat("If the receipt is a dinner over $50, flag it for HR review.")

    assert learned["route"] == "feedback"
    assert learned["status"] == "saved" and learned["verified"] is True
    rule = learned["rule"]
    assert rule["id"] == "R3" and rule["verified"] is True
    assert rule["example"] == {"case_id": sop_ledger.load_last_case()["case_id"], "expected": FLAG_DINNER}
    # The re-run used the candidate rule
    assert "R3: Flag dinners over $50." in reviewed.calls[-1][0]
    # The last case now holds the corrected review
    assert sop_ledger.load_last_case()["decisions"][1]["decision"] == "flag_for_review"

    # A new review uses the new rule
    reviewed.responses = [review("approve", "flag_for_review", rules=["R3"])]
    loop_harness.chat("Review this one.", REPORT)
    assert "R3: Flag dinners over $50." in reviewed.calls[-1][0]

    # The manager raises the limit; R4 supersedes R3
    reviewed.responses = [route("feedback"),
                          proposal("update", "Flag dinners over $75.", replaces="R3", expected=expect("2", "approve")),
                          review("approve", "approve", rules=["R4"])]
    updated = loop_harness.chat("Actually the limit is $75. That dinner was fine.")

    assert updated["status"] == "saved" and updated["rule"]["supersedes"] == "R3"
    ledger = sop_ledger.load_ledger()
    assert [(rule["id"], rule["status"]) for rule in ledger] == [
        ("R1", "active"), ("R2", "active"), ("R3", "superseded"), ("R4", "active")]
    assert ledger[2]["superseded_by"] == "R4"


def test_rule_is_revised_until_the_test_passes(reviewed):
    reviewed.responses = [route("feedback"),
                          proposal(rule_text="Flag meals.", expected=FLAG_DINNER), review("approve", "approve"),
                          proposal(rule_text="Flag dinners over $50.", expected=FLAG_DINNER),
                          review("approve", "flag_for_review")]

    result = loop_harness.chat("That dinner should have been flagged.")

    assert result["status"] == "saved" and result["rule"]["text"] == "Flag dinners over $50."
    revision_prompt = reviewed.calls[4][1]
    assert "Flag meals." in revision_prompt and '"got": "approve"' in revision_prompt
    assert [rule["id"] for rule in sop_ledger.load_ledger()] == ["R1", "R2", "R3"]


def test_nothing_is_saved_when_every_revision_fails(reviewed):
    ledger_before = sop_ledger.load_ledger()
    case_before = sop_ledger.load_last_case()
    reviewed.responses = [route("feedback")]
    for _ in range(loop_harness.MAX_RULE_REVISIONS + 1):
        reviewed.responses += [proposal(expected=FLAG_DINNER), review("approve", "approve")]

    result = loop_harness.chat("That dinner should have been flagged.")

    assert result["status"] == "not_saved"
    assert result["mismatches"] == [{"item_id": "2", "expected": "flag_for_review", "got": "approve"}]
    assert sop_ledger.load_ledger() == ledger_before
    assert sop_ledger.load_last_case() == case_before
    assert reviewed.responses == []


def test_failed_rerun_counts_as_a_mismatch(reviewed):
    reviewed.responses = [route("feedback"), proposal(expected=FLAG_DINNER), *["not json"] * 3,
                          proposal(expected=FLAG_DINNER), review("approve", "flag_for_review")]

    result = loop_harness.chat("That dinner should have been flagged.")

    assert result["status"] == "saved"
    assert '"got": null' in reviewed.calls[6][1]


def test_policy_statement_is_saved_unverified(fake_llm):
    fake_llm.responses = [route("feedback"), proposal(rule_text="Deny alcohol purchases.")]

    result = loop_harness.chat("From now on, deny any alcohol purchases.")

    assert result["status"] == "saved" and result["verified"] is False
    saved = sop_ledger.load_ledger()[-1]
    assert (saved["id"], saved["text"], saved["verified"], saved["example"]) == (
        "R3", "Deny alcohol purchases.", False, None)


def test_none_proposal_leaves_the_ledger_unchanged(reviewed):
    ledger_before = sop_ledger.load_ledger()
    reviewed.responses = [route("feedback"), json.dumps({"action": "none", "reply": "Thank you!"})]

    result = loop_harness.chat("Great job on that one.")

    assert result == {"route": "feedback", "status": "unchanged", "reply": "Thank you!"}
    assert sop_ledger.load_ledger() == ledger_before


def test_evolver_failure_saves_nothing(reviewed):
    ledger_before = sop_ledger.load_ledger()
    reviewed.responses = [route("feedback"), *["not json"] * 3]

    assert loop_harness.chat("Hmm.")["status"] == "error"
    assert sop_ledger.load_ledger() == ledger_before


def test_reset_memory_and_show_sops(reviewed, capsys):
    reviewed.responses = [route("feedback"), proposal("update", "Deny undated items.", replaces="R2")]
    loop_harness.chat("Change R2.")

    loop_harness.show_sops()
    shown = capsys.readouterr().out
    assert "R3: Deny undated items." in shown and "(replaced by R3)" in shown

    loop_harness.reset_memory()
    assert [(rule["id"], rule["text"], rule["status"]) for rule in sop_ledger.load_ledger()] == [
        ("R1", sop_ledger.SEED_RULES[0], "active"), ("R2", sop_ledger.SEED_RULES[1], "active")]
    assert sop_ledger.load_last_case() is None


def test_samples_are_valid_reports():
    for name in ("tr-1042", "tr-1051"):
        report = loop_harness.load_report(name)
        log_ids = [entry["item_id"] for entry in report["expense_log"]]
        assert log_ids == [receipt["item_id"] for receipt in report["receipts"]]
