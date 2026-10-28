import copy
import json

from evolvingagent02 import sop_ledger
from replies import REPORT


def _decision(item_id, decision="approve", rules=("R1",)):
    return {"item_id": item_id, "decision": decision, "reason": "because", "rules_applied": list(rules)}


def test_first_load_creates_seed_rules(memory_dir):
    ledger = sop_ledger.load_ledger()

    assert [rule["id"] for rule in ledger] == ["R1", "R2"]
    assert all(rule["source"] == "seed" and rule["status"] == "active" for rule in ledger)
    assert json.loads((memory_dir / "sop_ledger.json").read_text()) == ledger


def test_ledger_round_trips():
    ledger = sop_ledger.apply_change(sop_ledger.load_ledger(), {"action": "add", "rule_text": "New rule."})
    sop_ledger.save_ledger(ledger)

    assert sop_ledger.load_ledger() == ledger


def test_add_appends_an_unverified_rule_with_the_next_id():
    ledger = sop_ledger.seed_ledger()
    original = copy.deepcopy(ledger)
    example = {"case_id": "c-1", "expected": [{"item_id": "2", "decision": "flag_for_review"}]}

    candidate = sop_ledger.apply_change(ledger, {"action": "add", "rule_text": "Flag dinners."}, example=example)

    new_rule = candidate[-1]
    assert new_rule["id"] == "R3"
    assert new_rule["text"] == "Flag dinners."
    assert new_rule["source"] == "feedback"
    assert new_rule["example"] == example
    assert new_rule["verified"] is False
    assert ledger == original  # the input ledger is never changed


def test_update_supersedes_the_old_rule_and_links_both_ways():
    ledger = sop_ledger.apply_change(sop_ledger.seed_ledger(), {"action": "add", "rule_text": "Limit $50."})

    candidate = sop_ledger.apply_change(ledger, {"action": "update", "rule_text": "Limit $75.", "replaces": "R3"})

    old_rule, new_rule = candidate[2], candidate[3]
    assert (old_rule["status"], old_rule["superseded_by"]) == ("superseded", "R4")
    assert (new_rule["id"], new_rule["supersedes"], new_rule["status"]) == ("R4", "R3", "active")
    assert ledger[2]["status"] == "active"


def test_ids_are_never_reused_after_superseding():
    ledger = sop_ledger.apply_change(sop_ledger.seed_ledger(), {"action": "update", "rule_text": "x", "replaces": "R2"})
    ledger = sop_ledger.apply_change(ledger, {"action": "add", "rule_text": "y"})

    assert [rule["id"] for rule in ledger] == ["R1", "R2", "R3", "R4"]


def test_active_rules_and_format_skip_superseded_rules():
    ledger = sop_ledger.apply_change(sop_ledger.seed_ledger(), {"action": "update", "rule_text": "New.", "replaces": "R1"})

    assert [rule["id"] for rule in sop_ledger.active_rules(ledger)] == ["R2", "R3"]
    assert sop_ledger.format_rules(ledger) == f"R2: {sop_ledger.SEED_RULES[1]}\nR3: New."


def test_report_item_ids_include_log_entries_and_receipts():
    report = {"expense_log": [{"item_id": "1"}], "receipts": [{"item_id": "1"}, {"item_id": "9"}]}

    assert sop_ledger.report_item_ids(report) == {"1", "9"}


def test_check_decisions_accepts_a_valid_reply():
    reply = {"reply": "ok", "decisions": [_decision("1"), _decision("2", "deny", ["R1", "R2"])]}

    assert sop_ledger.check_decisions(reply, REPORT, sop_ledger.seed_ledger()) == []


def test_check_decisions_accepts_no_decisions_without_a_report():
    assert sop_ledger.check_decisions({"reply": "answer", "decisions": []}, None, sop_ledger.seed_ledger()) == []


def test_check_decisions_reports_schema_errors():
    reply = {"reply": "ok", "decisions": [{"item_id": "1", "decision": "maybe", "reason": "x", "rules_applied": []}]}

    errors = sop_ledger.check_decisions(reply, REPORT, sop_ledger.seed_ledger())

    assert len(errors) == 1 and "decisions/0/decision" in errors[0]


def test_check_decisions_requires_one_decision_per_report_item():
    ledger = sop_ledger.seed_ledger()

    missing = sop_ledger.check_decisions({"reply": "", "decisions": [_decision("1")]}, REPORT, ledger)
    extra = sop_ledger.check_decisions(
        {"reply": "", "decisions": [_decision("1"), _decision("2"), _decision("7")]}, REPORT, ledger)
    duplicate = sop_ledger.check_decisions(
        {"reply": "", "decisions": [_decision("1"), _decision("2"), _decision("2")]}, REPORT, ledger)

    assert missing and "exactly the report's items" in missing[0]
    assert extra and "exactly the report's items" in extra[0]
    assert duplicate and "exactly one decision" in duplicate[0]


def test_check_decisions_rejects_unknown_and_superseded_rule_ids():
    ledger = sop_ledger.apply_change(sop_ledger.seed_ledger(), {"action": "update", "rule_text": "x", "replaces": "R1"})
    reply = {"reply": "", "decisions": [_decision("1", rules=["R1"]), _decision("2", rules=["R9"])]}

    errors = sop_ledger.check_decisions(reply, REPORT, ledger)

    assert len(errors) == 2
    assert "['R1']" in errors[0] and "['R9']" in errors[1]


def test_last_case_round_trips_and_clears(memory_dir):
    assert sop_ledger.load_last_case() is None
    case = {"case_id": sop_ledger.new_case_id(), "message": "review", "report": REPORT, "reply": "", "decisions": []}

    sop_ledger.save_last_case(case)
    assert sop_ledger.load_last_case() == case

    sop_ledger.clear_last_case()
    assert sop_ledger.load_last_case() is None
    sop_ledger.clear_last_case()  # clearing twice is harmless
