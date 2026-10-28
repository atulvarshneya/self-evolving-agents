"""Loop control harness: one conversation turn at a time. Routes each message, then performs the task,
or learns from the feedback by testing the proposed rule on the last case before saving it."""
import json
import os

from . import sop_ledger, router, performer_agent, evolver_agent

# Times the Evolver may revise a rule that failed testing
MAX_RULE_REVISIONS = 2

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def load_report(name):
    """Load a sample report from samples/<name>.json."""
    with open(os.path.join(SAMPLES_DIR, f"{name}.json")) as f:
        return json.load(f)


def chat(message, report=None):
    """One conversation turn with the apprentice. Prints its progress and reply, and returns the outcome."""
    route = router.route_message(message, report is not None)
    if route == "task":
        return {"route": "task", **_perform(message, report)}
    return {"route": "feedback", **_learn(message)}


def _perform(message, report):
    ledger = sop_ledger.load_ledger()
    result = performer_agent.run_performer(message, report, ledger)
    if result["status"] != "success":
        print(f"\n❌ {result['message']}")
        return result

    case = {"case_id": sop_ledger.new_case_id(), "message": message, "report": report,
            "reply": result["reply"], "decisions": result["decisions"]}
    sop_ledger.save_last_case(case)
    print(f"\n💬 {result['reply']}")
    _print_decisions(result["decisions"])
    return {**result, "case_id": case["case_id"]}


def _learn(feedback):
    ledger = sop_ledger.load_ledger()
    last_case = sop_ledger.load_last_case()
    previous_attempt = None

    for revision in range(MAX_RULE_REVISIONS + 1):
        outcome = evolver_agent.propose_change(feedback, last_case, ledger, previous_attempt)
        if outcome["status"] != "success":
            print(f"\n❌ {outcome['message']}")
            return outcome
        proposal = outcome["proposal"]

        if proposal["action"] == "none":
            print(f"\n💬 {proposal['reply']}\n(No change to the SOP rules.)")
            return {"status": "unchanged", "reply": proposal["reply"]}

        expected = proposal.get("expected")
        if not expected or last_case is None:
            # A policy statement with no case to re-run: saved, but marked untested
            candidate = sop_ledger.apply_change(ledger, proposal)
            sop_ledger.save_ledger(candidate)
            rule = candidate[-1]
            print(f"\n💬 {proposal['reply']}\n📘 Saved {rule['id']} (untested: no case to check it on): {rule['text']}")
            return {"status": "saved", "rule": rule, "verified": False, "reply": proposal["reply"]}

        candidate = sop_ledger.apply_change(ledger, proposal,
                                            example={"case_id": last_case["case_id"], "expected": expected})
        print(f"[Harness] Testing the rule by reviewing the last case again...")
        rerun = performer_agent.run_performer(last_case["message"], last_case["report"], candidate)
        mismatches = _mismatches(expected, rerun)

        if not mismatches:
            candidate[-1]["verified"] = True
            sop_ledger.save_ledger(candidate)
            # The last case now shows the corrected review, so further feedback builds on it
            sop_ledger.save_last_case({**last_case, "reply": rerun["reply"], "decisions": rerun["decisions"]})
            rule = candidate[-1]
            print(f"\n💬 {proposal['reply']}\n📘 Saved {rule['id']} (tested on case {last_case['case_id']}): "
                  f"{rule['text']}")
            if rule["supersedes"]:
                print(f"   It replaces {rule['supersedes']}, which is kept as superseded.")
            return {"status": "saved", "rule": rule, "verified": True, "reply": proposal["reply"]}

        previous_attempt = {"rule_text": proposal["rule_text"], "replaces": proposal.get("replaces"),
                            "expected": expected, "mismatches": mismatches}
        if revision < MAX_RULE_REVISIONS:
            print(f"[Harness] The rule didn't produce the expected decisions {mismatches}; asking the Evolver to "
                  f"revise it (revision {revision + 1} of {MAX_RULE_REVISIONS}).")

    print(f"\n❌ I couldn't write a rule that fixes this case, so I haven't saved one.\n"
          f"   Last draft: {proposal['rule_text']}\n   Still wrong: {mismatches}")
    return {"status": "not_saved", "rule_text": proposal["rule_text"], "mismatches": mismatches,
            "reply": proposal["reply"]}


def _mismatches(expected, rerun):
    """The expected decisions the re-run got wrong, as [{item_id, expected, got}]."""
    got = {d["item_id"]: d["decision"] for d in rerun.get("decisions", [])} if rerun["status"] == "success" else {}
    return [{"item_id": e["item_id"], "expected": e["decision"], "got": got.get(e["item_id"])}
            for e in expected if got.get(e["item_id"]) != e["decision"]]


def _print_decisions(decisions):
    icons = {"approve": "✅", "flag_for_review": "🚩", "deny": "⛔"}
    for d in decisions:
        print(f"   {icons.get(d['decision'], '')} item {d['item_id']}: {d['decision']} - {d['reason']} "
              f"[{', '.join(d['rules_applied'])}]")


def show_sops():
    """Print the active rules, then the superseded ones."""
    ledger = sop_ledger.load_ledger()
    print("📘 Active SOP rules:")
    for rule in sop_ledger.active_rules(ledger):
        tag = "seed" if rule["source"] == "seed" else ("tested" if rule["verified"] else "untested")
        print(f"   {rule['id']}: {rule['text']}  ({tag})")
    superseded = [rule for rule in ledger if rule["status"] == "superseded"]
    if superseded:
        print("🗄️  Superseded rules:")
        for rule in superseded:
            print(f"   {rule['id']}: {rule['text']}  (replaced by {rule['superseded_by']})")


def reset_memory():
    """Restore the seed rules and forget the last case."""
    sop_ledger.save_ledger(sop_ledger.seed_ledger())
    sop_ledger.clear_last_case()
    print("🧹 Memory reset to the seed rules.")
