# evolvingagent02: the Apprentice (design)

`evolvingagent01` evolves by writing new **tools**. `evolvingagent02` evolves its **knowledge** instead. It's an apprentice expense clerk. When its manager corrects it, it turns the correction into a permanent, general rule in its Standard Operating Procedure (SOP) ledger, so it doesn't repeat the mistake.

```
       [ Manager message ] ──(+ optional expense report)
                │
                ▼
           [ Router ] ── task ──> [ Performer ] <── active rules ── [ SOP ledger ]
                │                       │                                 ▲
             feedback                   ▼                                 │ saves the rule,
                │               [ decisions + reply ] ──> last case       │ only if verified
                ▼                                            │            │
           [ Evolver ] <──── last case + active rules ───────┘            │
                │                                                         │
                ▼                                                         │
     [ proposed rule change ] ──> re-run the last case with the rule ─────┘
                                   (revise the rule and retry if the result is still wrong)
```

This design follows `evolvingagent01`'s patterns and the conventions in the [root CLAUDE.md](../../CLAUDE.md).

## 1. Goal and scenario

A Junior Travel & Expense Assistant reviews expense reports by matching receipts against the expense log. It starts with two seed rules:

- **R1:** Match each receipt amount to its expense log entry. Flag the item for review if the amounts differ.
- **R2:** Deny any expense that lacks a valid merchant name or date.

The manager reviews its work and gives natural feedback, for example *"Hey, if the receipt is a dinner over $50, flag it for HR review."* The agent:
1. turns that into a general rule
2. checks that the rule actually fixes the case it got wrong
3. saves the rule permanently

Every later report is then reviewed with the new rule.

## 2. Starting point: problems in the reference code

We started from example code found online. This design keeps its overall pattern (a Performer that follows the rules, and an Evolver that writes them) but fixes these problems:

| Problem in the reference code | How this design handles it |
|---|---|
| The Performer handles every message, including feedback, as a task; whether a message was feedback is only decided afterwards | Each message is routed **before** anything else runs (§5) |
| The Evolver sees only the feedback text, not the task and answer being corrected | The last case (task, report and decisions) is saved and given to the Evolver (§4, §7) |
| The LLM rewrites the **whole** ledger and numbers the rules itself, so rules can be silently dropped or changed | The LLM proposes **one change** (add, update or none). Code assigns IDs and edits the ledger (§4) |
| Contradictory rules pile up | An update supersedes the old rule. Superseded rules are kept for history but never shown to the Performer (§4) |
| Learned rules are never checked | The corrected case is re-run with the new rule, and the rule is saved only if the result is right (§7) |
| Unchecked JSON replies can corrupt memory or crash the program | Every reply goes through `parse_json()` and a JSON Schema check, and calling mistakes are retried, as in `evolvingagent01` |
| A hardcoded Anthropic client, small `max_tokens`, and files saved relative to the current folder | `evolvingagent01`'s `llm.py` is reused (provider code in one place, settings from `.env`), and memory is stored relative to the package folder |

## 3. Package layout

`evolvingagent02` is a self-contained package. It doesn't import from `evolvingagent01`, and it shares only the root `.env`.

```
evolvingagent02/
  __init__.py
  llm.py               copy of evolvingagent01/llm.py (call_llm, parse_json), unchanged
  sop_ledger.py        rule records, seed rules, ledger load/save/apply, last case, decision schema and checks
  router.py            route_message(): "task" or "feedback"
  performer_agent.py   run_performer(): review a task using the active rules; returns structured decisions
  evolver_agent.py     propose_change(): turn feedback into one add / update / none proposal
  loop_harness.py      chat(), show_sops(), reset_memory(); routes, verifies and saves
  memory/              sop_ledger.json, last_case.json   (created on first run)
  samples/             sample expense reports (JSON)
  run_evolvingagent02.ipynb
  docs/DESIGN.md   requirements.txt   tests/   CLAUDE.md
```

**Import order:** imports only go downward, as in `evolvingagent01`. `llm` and `sop_ledger` import nothing from the project. `router`, `performer_agent` and `evolver_agent` import only those two. `loop_harness` imports everything. Rule verification needs both the Evolver and the Performer, so it lives in `loop_harness`. That way neither agent imports the other.

**Module imports:** modules import each other as modules (`from . import llm`) and call through them (`llm.call_llm(...)`), so the tests' `fake_llm` fixture works everywhere.

**Dependencies:** `anthropic`, `python-dotenv`, `jsonschema` and `pytest`, the same as `evolvingagent01`.

## 4. The SOP ledger (`sop_ledger.py`)

The ledger is stored in `memory/sop_ledger.json` as a list of rule records:

```json
{
  "id": "R3",
  "text": "If a receipt is for a dinner over $50, flag the item for HR review.",
  "status": "active",
  "source": "feedback",
  "created_at": "2026-10-27T10:15:00",
  "supersedes": null,
  "superseded_by": null,
  "example": {"case_id": "c-0007", "expected": [{"item_id": "3", "decision": "flag_for_review"}]},
  "verified": true
}
```

| Field | Meaning |
|---|---|
| `id` | `R<n>`, assigned by code. IDs increase and are never reused. The LLM never chooses them. |
| `status` | `active` or `superseded`. Only active rules are shown to the Performer. |
| `source` | `seed` or `feedback` |
| `supersedes` / `superseded_by` | Link a replacement rule and the rule it replaced, in both directions |
| `example` | The case the rule was learned from, and the decisions the manager expected. `null` for seed rules and for policy statements made with no case. |
| `verified` | `true` if re-running the example produced the expected decisions. `false` for policy statements, which have no case to re-run (§7). |

Functions:
- **`load_ledger()` / `save_ledger(ledger)`.** On first load, the file is created from `SEED_RULES` (via `seed_ledger()`).
- **`apply_change(ledger, proposal, example=None)`** is a pure function: it returns a **candidate** ledger and saves nothing. With `add`, it appends a new rule. With `update`, it marks the old rule `superseded`, adds the replacement, and links the two. The new rule is always the candidate's last record, with `verified: false` until the harness tests it.
- **`active_rules(ledger)`** returns the active rule records. **`format_rules(ledger)`** formats them as `R<n>: <text>` lines for the LLM's prompts.
- **`report_item_ids(report)`** returns every item ID in a report, from both its expense log and its receipts.
- **`check_decisions(reply, report, ledger)`** checks a Performer reply (§6).
- **`load_last_case()` / `save_last_case(case)` / `clear_last_case()`** handle the most recent task: its message, report, reply and decisions. It's kept on disk, so feedback can refer to it even after the notebook kernel restarts. Each case gets a `case_id` from `new_case_id()`.
- **`MEMORY_DIR`** is the memory folder's location, relative to the package folder rather than the current folder, like `evolvingagent01`'s `TOOLS_DIR`. Tests point it at a temporary folder.

## 5. Routing (`router.py`)

`route_message(message, has_report)` decides what a message is:

- **A message with an attached report is always a task.** No LLM call is needed.
- **Otherwise, one small LLM call** returns `{"route": "task" | "feedback"}`:
  - a *task* is a request or question, such as "What's our policy on dinners?"
  - *feedback* is a correction, a new instruction or a policy statement
- **If the reply can't be parsed,** the call is retried up to `MAX_CALL_RETRIES = 2` times. If it still fails, the message is treated as a task. That's the safe default, because a task never changes the ledger.

## 6. The Performer (`performer_agent.py`)

`run_performer(message, report, ledger)` sends the LLM the active rules, the manager's message and the report (if any), and expects:

```json
{
  "reply": "Message to the manager.",
  "decisions": [
    {"item_id": "3", "decision": "flag_for_review",
     "reason": "Dinner of $72.40 is over the $50 limit.", "rules_applied": ["R1", "R3"]}
  ]
}
```

`decision` is one of `approve`, `flag_for_review` or `deny`.

`check_decisions()` requires that:
- the reply matches the JSON Schema
- when a report is attached, there is exactly one decision per report item, with matching `item_id`s
- every ID in `rules_applied` is an active rule

A failed check is a **calling mistake**. The Performer asks the LLM again, including what was wrong, up to `MAX_CALL_RETRIES = 2` times. This is the same approach as `evolvingagent01`'s Performer. If it still fails, the manager is told the review couldn't be completed. A question with no report returns `decisions: []`.

When several rules apply, the prompt tells the LLM that the strictest outcome wins (deny, then flag_for_review, then approve).

The harness saves a successful review as the last case. The Performer itself saves nothing, so re-running a case to test a rule doesn't overwrite it.

## 7. The Evolver, and testing new rules

`propose_change(feedback, last_case, ledger, previous_attempt=None)` asks the LLM for **one** proposed change:

```json
{
  "action": "add",
  "rule_text": "If a receipt is for a dinner over $50, flag the item for HR review.",
  "replaces": null,
  "expected": [{"item_id": "3", "decision": "flag_for_review"}],
  "reply": "Understood. I'll flag dinners over $50 for HR review from now on."
}
```

| `action` | Used when |
|---|---|
| `add` | The feedback teaches something new |
| `update` | The feedback changes or contradicts an existing rule, for example raising the dinner limit to $75. `replaces` names that active rule. |
| `none` | Praise, or feedback already covered by an existing rule. Only `reply` matters. |

The Evolver's prompt tells it to write rules that are **general**. They shouldn't mention specific names, dates or amounts unless the amount is itself the policy, like the $50 limit. `expected` lists the decisions the manager wanted for the last case's items. It's `null` when the feedback doesn't refer to a case.

**Checking the proposal.** The Evolver's `check_proposal()` checks that the reply matches the proposal's JSON Schema, that `rule_text` is set for add and update, that `replaces` names an active rule (and is null for an add), and that every `expected` item ID exists in the last case's report. A failed check is a calling mistake, retried up to `MAX_CALL_RETRIES = 2` times. The harness then tests the rule:

1. **If the feedback corrects a case** (`expected` is set and a last case exists):
   1. Build the candidate ledger with `apply_change()`.
   2. Re-run the Performer on the last case, using the candidate ledger.
   3. Compare its decisions with `expected`, for those items only.
   4. **If they match,** save the candidate ledger. The new rule is saved with `verified: true` and the case as its `example`. The last case is updated with the re-run's decisions, so further feedback builds on the corrected review.
   5. **If they don't,** call the Evolver again with `previous_attempt`: the rule it wrote and the mismatches (`item_id`, `expected`, `got`). A re-run that fails outright counts as a mismatch with `got: null`. Repeat up to `MAX_RULE_REVISIONS = 2` times.
   6. **If it still fails,** save nothing. Tell the manager what the drafted rule was and which decisions were still wrong.
2. **If it's a policy statement with no case,** such as "From now on, deny all alcohol": save the rule with `verified: false`, since there's nothing to re-run.

This is the agent's version of `evolvingagent01`'s write, run, fix loop: a rule is kept only when it has been shown to change the decision it was meant to change.

## 8. The loop harness and notebook (`loop_harness.py`)

- **`chat(message, report=None)`** is one conversation turn:
  1. Route the message.
  2. Run either the Performer, or the Evolver with rule testing.
  3. Print progress with `[Router]`, `[Performer]` and `[Evolver]` prefixes, as in `evolvingagent01`.

  It returns the result, so tests can check it.
- **`show_sops()`** prints the active rules, then the superseded ones.
- **`reset_memory()`** restores the seed rules and clears the last case.
- **`load_report(name)`** loads `samples/<name>.json`.

The driver is **`run_evolvingagent02.ipynb`**, set up the same way as `evolvingagent01`'s notebook: `%autoreload 2`, with the repo root added to `sys.path`. It has one cell per conversation turn, and loads reports from `samples/`. The demo script:

1. **Review `tr-1042`, which includes a $72.40 dinner.** With only the seed rules, the agent approves it.
2. **The manager gives the dinner feedback.** Rule R3 is added, tested against that report, and saved.
3. **Review `tr-1051`.** Its $68 dinner is flagged under R3. The report also has an amount mismatch (R1) and a missing merchant (R2).
4. **The manager raises the limit to $75 and says that dinner was fine.** R4 replaces R3, which is kept as superseded. R4 is tested on `tr-1051`, where the dinner must now be approved.
5. **A policy statement** ("From now on, deny any alcohol purchases") is saved untested.

### Sample report format (`samples/*.json`)

```json
{
  "report_id": "TR-1042",
  "employee": "A. Rivera",
  "expense_log": [
    {"item_id": "1", "date": "2026-10-12", "merchant": "Hotel Nova", "category": "lodging", "amount": 189.00},
    {"item_id": "3", "date": "2026-10-13", "merchant": "Bistro 9",   "category": "meal",    "amount": 72.40}
  ],
  "receipts": [
    {"item_id": "1", "merchant": "Hotel Nova", "date": "2026-10-12", "amount": 189.00, "description": "1 night"},
    {"item_id": "3", "merchant": "Bistro 9",   "date": "2026-10-13", "amount": 72.40,  "description": "dinner"}
  ]
}
```

The samples should cover each seed rule (a mismatched amount, a missing merchant) and the dinner scenario.

## 9. Tests (`tests/`)

The approach is the same as `evolvingagent01`'s, with one test file per module. Tests never call the real LLM API and never touch the real `memory/` folder.

- **`conftest.py`** adds the repo root to `sys.path` and sets a dummy API key. It provides two fixtures:
  - `fake_llm`: replaces `llm.call_llm` with scripted replies
  - `memory_dir` (autouse): points `MEMORY_DIR` at a temporary folder for every test

  `tests/replies.py` builds the scripted replies (`route()`, `review()`, `proposal()`, `expect()`) and a small sample `REPORT`.
- **`test_sop_ledger.py`:**
  - seeding on first load, and ID assignment
  - `add` and `update` in `apply_change()`, including the superseding links, and leaving the original ledger unchanged
  - `active_rules()`
  - `check_decisions()`: the schema, item IDs, and unknown or superseded rule IDs
  - last-case persistence
- **`test_router.py`:** a report means a task with no LLM call; the task and feedback routes; an unparseable reply falls back to a task.
- **`test_performer.py`:** a successful review; retries for schema, item-ID and rule-ID mistakes; giving up after the retries.
- **`test_evolver.py`:**
  - the prompt includes the last case and the active rules
  - each proposal type is read correctly
  - `previous_attempt` appears in the retry prompt
- **`test_loop_harness.py`:**
  - the full demo script from §8
  - testing a rule fails, then succeeds after a revision
  - it fails every time, and the ledger is unchanged
  - a policy statement is saved with `verified: false`
  - a `none` proposal leaves the ledger unchanged

## 10. Out of scope (possible later work)

- **Re-running every past case** when a rule is added, to catch a new rule breaking an old lesson. The `example` stored on each rule makes this possible later.
- **Merging or tidying rules** as the ledger grows.
- **Multiple managers,** or feedback that conflicts by authority.
- **Reading real receipt images** instead of JSON.
