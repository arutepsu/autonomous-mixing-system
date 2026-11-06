# Decision Gate Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Work sequentially in the current session.

**Goal:** Route structurally checked decisions and block proposals whose selected candidates lack sufficient available evidence.

**Architecture:** Immutable results in core.records.decision_gate. Pure assessment in core.admission.decision_gate, using the existing decision port validator. The future loop consumes the result.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Owner-approved gate contract below, extending sections 12–15 of docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md. Requires the completed 2E outcome contract.

## Contract

`assess_decision(situation: Situation, response: DecisionResponse) -> DecisionGateResult` runs after PLAN/DECIDE and before ACT. It reads only supplied records. Use the existing invariant helpers.

Define these frozen result records in core.records.decision_gate:

| Record | Fields and invariants |
| --- | --- |
| InvalidResponse | `explanation: str`, nonblank |
| KeepState | `decision: Decision` with NoChange outcome |
| RequestContext | `decision: Decision` with MISSING_CONTEXT Abstain outcome. Read the original questions from decision.outcome.requested_context |
| DeclineAction | `decision: Decision` with any other Abstain outcome. Read the original reason from decision.outcome.reason |
| EvidenceBlocked | `decision: Decision` with ProposeAction outcome, `blocked_candidate_ids: tuple[ProblemCandidateId, ...]`, nonempty, unique and a subsequence of the proposal's candidate IDs |
| ReadyForAdmission | `decision: Decision` with ProposeAction outcome |

`DecisionGateResult` is the union of those six classes. Use required fields with no defaults. Keep the original decision object. Invalid result construction raises ValueError for invalid supplied values.

1. Check argument types before entering the response-validation exception handler. Call validate_decision_response once. Convert its ValueError to InvalidResponse with the original explanation. Unexpected exceptions propagate. Every route, including non-actions, follows successful structural validation.
2. Route NoChange to KeepState, MISSING_CONTEXT to RequestContext, and other abstentions to DeclineAction.
3. For each selected candidate, in proposal order, block it if its diagnostic evidence is marked insufficient OR none of its own evidence IDs resolves to an observation whose result is other than Unavailable. An available observation elsewhere in the decision cannot rescue it. Partial availability passes for a candidate when at least one of its own observations is available and the insufficient flag is false. A measured zero is available. Unselected candidates impose no restriction.
4. Return EvidenceBlocked with all blocked IDs in proposal order, or ReadyForAdmission when none are blocked.

ReadyForAdmission hands off to authoritative admission. This step does not apply scope, capability or gain permission rules, call admission, or infer missing musical context. KeepState leaves any success verdict to deterministic verification. RequestContext carries the information needed for the future loop to end the run with the last accepted state. Updated context starts a new run. No loop routing, rendering, budget charging or persistence is implemented here.

## Global constraints and owned paths

Read CLAUDE.md, applicable AGENTS.md and required project references. Preserve existing work. Work sequentially without subagents. Keep the dependency graph acyclic and package initializers docstring-only. Use labelled metadata fixtures. No new scientific thresholds, detectors, dependency changes, installs, commits or pushes.

- New src/autonomous_mixing/core/records/decision_gate.py and src/autonomous_mixing/core/admission/decision_gate.py.
- New tests/unit/test_decision_gate.py.
- tests/architecture/test_core_records.py adds the new record inventory and exact used edges to decision, identifiers and invariants. tests/architecture/test_core_layers.py adds admission.decision_gate with assess_decision. Keep all existing restrictions.
- Update docs/architecture/overview.md in place and append to docs/research/research-log.md. Plan checkbox updates are allowed. Report any additional required path before editing it.

## Review focus

- An invalid NoChange or context request must still go through response validation.
- A measurement from another candidate cannot compensate for unavailable evidence.
- One available measurement does not override an explicit insufficiency flag.
- Blocked candidate IDs preserve proposal order even when diagnosis order differs.
- Propagate unexpected failures and preserve original decisions and context questions.

## Task 1

- [ ] Record actual HEAD and changed paths. Confirm 2E contracts are present. Run baseline tests, lint, format, CLI help and lock check with existing tools.
- [ ] Add focused tests for the six result shapes, frozen records and union, wrong types, blocked ID subsequence checks, all decision routes, structural precedence and unexpected exception propagation. Exercise partial availability, measured zero, all-unavailable evidence, explicitly insufficient evidence with available observations, irrelevant available observations and multiple blocked candidates. Run the tests red.
- [ ] Implement the two modules and update the exact architecture inventories. Run focused tests and architecture checks. Verify existing admission tests still exercise their original policy checks.
- [ ] Append evidence and limits to the research log and update the overview. Record that the gate is standalone and no requirement status changes.
- [ ] Run uv run --locked pytest -q -rs, uv run --locked ruff check ., uv run --locked ruff format --check src tests, uv run --locked autonomous-mixing --help and uv lock --check. If uv is unavailable, use repository tools and report any omitted check. Inspect the final diff against ownership.
- [ ] Return exact signatures, tests and results, deviations, remaining gaps and git status. Leave the work uncommitted for Codex review.
