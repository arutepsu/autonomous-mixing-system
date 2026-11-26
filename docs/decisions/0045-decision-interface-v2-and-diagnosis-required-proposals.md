# ADR-045 — Decision interface v2 and diagnosis-required proposals (2026-11-26)

- **Status:** Proposed. Implemented with regression tests that fail against the previous code (written alongside it, not strictly before), uncommitted for owner review. No provider call, MIDI operation, threshold change or live run was made.
- **Source:** the first live provider trial `ht01-observe-001` (research log, 2026-11-26) and the owner's approval on 2026-11-26 of policy A, per-call schemas, a model-text-only regression fixture, no v1 parser, and four amendments (capability versus permission including a zero-write budget; no empty enums; abstention-reason wording; version-labelled historical records).
- **Relation:** refines the R3/R4 decision adapter (`adapters.decision_agent`). Does not change core records, admission, verification or provenance gating ([ADR-041](0041-provider-run-evidence-gate.md)).

## Context

In `ht01-observe-001` the provider returned a `propose_action` whose `evidence` items were aliases with appended prose and whose `candidates` were invented action descriptions. The parser rejected it correctly, and nothing was written. The packet contributed to the failure:

- The core already required a proposal to address at least one supplied diagnosis candidate (`ProposeAction.candidate_ids` non-empty; `validate_decision_response`).
- The packet had zero candidates but still advertised a gain control and an unmet level target.
- Nothing said that no valid proposal existed, what a reference must look like, or that `candidates` meant diagnosed problems.

## Decision

1. **Policy A, stated explicitly.** A proposal must address at least one supplied diagnosis problem candidate. The packet carries `decision_options.proposal_basis = "supplied_problem_candidate"`. An unmet acceptance criterion is not a diagnosed problem and not a proposal basis. No diagnosis is derived from criteria, and no threshold is changed.
2. **Packet `autonomous_mixing.decision_packet@2`.**
   - `diagnosis.candidates` becomes `diagnosis.problem_candidates`, and a candidate's `evidence` becomes `evidence_refs`.
   - A new `decision_options` block states:
     - whether `propose_action` is available, with factual reasons when it is not (`no_problem_candidates`, `no_advertised_gain_control`);
     - exhausted execution budgets in `execution_limits` (`no_remaining_attempts`, and `no_remaining_daw_writes` on the DAW path or `no_remaining_render_calls` offline);
     - `no_change` and `abstain`, which are always available, with one meaning per abstention reason.
   - Glossary entries separate capability from permission, problems from actions, and criteria from problems, and define exact references.
   - An exhausted budget does not hide the proposal option. It says that nothing would be executed in this run.
3. **Response schema `autonomous_mixing.decision_response@2`.**
   - The reference fields are `addressed_problem_candidates` and `evidence_refs`, alongside `action.track`.
   - The family schema stays closed.
   - Per call, `response_schema_for(packet)` restricts reference items to that packet's exact aliases as string enums.
   - It omits `propose_action` exactly when the packet says the option is unavailable.
   - No enum is ever empty. A missing candidate set or gain control omits the whole `propose_action` variant.
   - **Contextual evidence shape.** When the packet has no current observation, `evidence_refs` is left out of every variant. The parser then requires the field to be absent and builds an empty evidence tuple. This is an explicit response shape for that context, not repair: an `evidence_refs` field (even `[]`), unexpected fields and invalid references are still refused. When observations exist, `evidence_refs` is required and restricted to their exact aliases.
   - The provider conversion keeps documented `minItems` values 0 and 1 (the proposal's `addressed_problem_candidates` and `evidence_refs` carry `minItems: 1`) and strips only undocumented constraints (`minLength`, other `minItems`), which the parser enforces.
   - Amended 2026-11-26 (before any commit or live use of version 2): an earlier draft constrained an empty evidence list with an unsatisfiable item object. It was replaced because documented keywords alone do not establish that the provider's grammar compiler accepts that combination.
4. **Prompt `autonomous_mixing.claude_prompt@2`.** States the policy, the reference rule and that abstention is normal. It gives one illustrative shape per outcome, using placeholder aliases (`obs-000000000000`, ...) that exist in no packet.
5. **The parser remains the authority.**
   - No trimming, prefix extraction or fuzzy matching.
   - A proposal without a supplied candidate is still refused locally, whatever the provider schema allowed.
   - Invalid-response messages now carry a first-failure category, used only for counting: `json`, `encoding`, `shape`, `type`, `value`, `reference.unknown`, `reference.wrong_kind`, `reference.historical`, `reference.duplicate`, `contract`, `envelope` or `size`.
6. **Historical records.** Version 1 exchanges stay readable under their recorded labels. They are never reinterpreted or revalidated:
   - the v2 parser raises a wiring error for a request recorded under another response schema;
   - the Claude adapter refuses to replay a stored exchange with other prompt or schema versions;
   - the existing runtime binding check refuses to reopen a v1-bound run;
   - `daw status` still prints recorded labels.
   - A stored completion record is presented only as what was recorded at the time.

## Objective-driven actions (policy B)

Acting on an explicit, unmet producer objective can be legitimate automation if it is represented honestly as its own justification basis. It is not inherently a fabricated diagnosis. It is out of scope here and needs its own ADR. That ADR would cover:

- a distinct basis in `ProposeAction`;
- a deterministic unmet-objective check at the gate;
- provenance of the cited level observations;
- separate evaluation labelling, so that such runs are never counted as masking corrections.

## Evaluation measures (definitions, not results)

These are counted from audit and run stores, each with an explicit denominator.

1. **Response validity:** valid parses out of complete envelopes, by first-failure category.
2. **Outcome mix:** proposals, `no_change` and abstentions (by reason), crossed with whether `propose_action` was available, candidate count and coverage status.
3. **Consistency:** `no_change` or `abstain` whenever a proposal is unavailable.
4. **Abstentions when a candidate exists** are reported by their stated reason and by available facts, not labelled over-abstention. Such facts include an insufficient or weak candidate statement, no admissible action, conflicting constraints or exhausted budgets.
5. **Admissible proposals:** ready for admission, then admitted.
6. **Safeguard refusals by layer:** parser, structural contract, gate, admission, preflight, budget.
7. **Verified outcomes:** executed, verification verdict, accepted or rolled back.

Frequent refusal or abstention alone is not success. Provider reliability needs repeated, separately authorized live calls. Deterministic tests show contract correctness only.

## Consequences

- Runs started with this code bind prompt and response schema version 2. Runs bound to version 1 (including the terminal `ht01-observe-001`) cannot be reopened and remain readable.
- While the experimental P1 diagnosis supplies no candidate, a live run can only keep the state or abstain. That is the expected honest outcome under policy A.
- Regression evidence lives in `tests/unit/test_decision_interface_v2.py`. It uses the verbatim captured model text (`tests/fixtures/ht01_observe_001_model_text_v1.json`, SHA-256 `87d04fd6…125d`; model text only).

## Open questions

- Whether the provider's structured-output mode accepts the per-call schema as sent: string enums of this size, descriptions, the `anyOf` shape without `propose_action`, `minItems: 1`, and variants without `evidence_refs`. Every keyword is documented as supported, but the schemas are untested live; the in-test validator checks local semantics only. The parser does not depend on provider enforcement.
- Whether the clearer interface lowers invalid-response rates. That requires authorized repeated trials, not this change.
