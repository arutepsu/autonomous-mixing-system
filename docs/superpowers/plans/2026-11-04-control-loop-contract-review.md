# Control Loop Contract Design Plan

> **For agentic workers:** This is a design plan, not authorization to implement code. Work serially, one deliverable at a time. After an implementation design is approved, use superpowers:writing-plans to create its implementation plan and superpowers:executing-plans for execution. Preserve the owner's workflow: Claude implements one scoped prompt, Codex reviews, then the owner commits.

**Goal:** Resolve the control-loop contracts around the owner's source lifecycle before implementing orchestration.

**Architecture:** Deterministic orchestration in `core.loop`, pure diagnosis/admission/verification rules, immutable records and adapter ports. The lifecycle is OBSERVE → DIAGNOSE → PLAN / DECIDE → ACT → EVALUATE → STOP / ITERATE / ROLLBACK, with evidence/context gating and bounded resource use.

**Tech stack:** Existing Python package, uv environment, pytest and Ruff; no new dependencies for contract design.

**Spec:** `docs/superpowers/specs/2026-11-04-control-loop-lifecycle-design.md`.

## Global constraints

- Treat the source lifecycle in the spec as a standing requirement in every subsequent prompt.
- Respect ADR-027 ownership, records immutability, source-audio preservation and SR-10.
- EVALUATE in the diagram maps to runtime `core.verification`; hidden research evaluation never enters the loop.
- Only supported actions are executable; currently gain rendering exists.
- No guessed musical context, invented detector method, arbitrary confidence calibration or unapproved threshold.
- Do not change accepted ADRs, implementation code, dependencies or requirement statuses during this design task.
- Proposed API simplifications and pause/resume behavior are unresolved until recorded as design decisions.

## Review focus

1. A second accepted attempt must start from the last accepted state, while using the fixed target input for absolute gain rendering.
2. A renderer can fail after creating one output: keep ownership, evidence and uncertainty visible; do not claim successful restoration without integrity evidence.
3. Missing context or unavailable measurements must never be interpreted as successful verification.
4. Rejected proposals and retries must consume bounded resources; an attempt budget alone cannot bound all decision calls.
5. Workspace-bound adapters must support multiple attempts without core filesystem access or accidental reuse of outputs/identities.

## Task 1: Walk through one attempt and its state ownership

**Files:** Read the spec, migration-plan section 5, engineering conventions, requirements, existing records/ports, `core.loop.transitions`, `core.loop.references`, admission/preflight, verification consistency and the concrete renderer. Create `docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md` as a proposed design.

**Deliverable:** A stage-by-stage table: input, owner, output, invariants, side effects, charged resources and recorded evidence. Use current names. Clearly label every proposed change.

- [x] Follow the existing contracts through one successful attempt without writing code.
- [x] Trace the initial state, last accepted state and candidate; identify who creates execution identities, resources and receipts.
- [x] Add paper walkthroughs for a rejected action, a regression and a render failure after partial output.
- [x] Resolve each duplicated/wrapped record only where the walkthrough demonstrates its retained purpose or redundancy.
- [x] List the decisions needing owner input, including missing-context behavior, separately from implementation details the engineer can settle.
- [x] Review this deliverable before starting Task 2. No implementation prompt yet.

## Task 2: Define observation, diagnosis and decision context

**Files:** Extend the attempt contract's OBSERVE / DIAGNOSE / PLAN sections; do not create record classes yet.

- [x] Specify what `Situation` must contain and what is captured verbatim in history.
- [x] Specify the distinction between diagnosis strength and action justification without inventing calibrated probabilities.
- [x] Define No Change / Abstain / Request Context and evidence/context gating, using the owner's answer on run termination versus pause/resume.
- [x] Trace absent candidates, missing context, conflicting priorities and an invalid decision response; identify later scientific-method work separately.
- [x] Review and record the selected contracts and their requirement links (FR-05, FR-06, FR-11, FR-12, QR-03, SR-10).

## Task 3: Define verification and restoration semantics

**Files:** Extend the attempt contract's EVALUATE and ITERATE / ROLLBACK sections.

- [x] Specify before/after compatibility, last-accepted versus fixed baselines, targets, minimum improvement, guards and unavailable evidence.
- [x] Write a complete outcome table: success, accepted intermediate progress, rejected candidate, inconclusive assessment, restoration success/failure and uncertain final state.
- [x] Demonstrate that a satisfied target does not additionally require minimum improvement, and that failing guards cannot be accepted.
- [x] Define retained artifacts, accepted-reference selection and integrity evidence for rollback; trace the second attempt after both an acceptance and a rejection.
- [x] Review before planning verifier code or new integrity/resource ports.

## Task 4: Define bounded run history and termination

**Files:** Extend the attempt contract with run-level semantics and a proposed record/port ownership map.

- [x] Define counters before calls, attempt accounting, monotonic elapsed-time accounting, bounded retries and recovery disposition at limits.
- [x] Give every terminal path an explicit outcome/reason; distinguish success, abstention, limits, failure and uncertainty.
- [x] Trace every Review Focus condition and all completion scenarios listed in the source-lifecycle spec.
- [x] Define returned trace versus durable persistence, provider provenance and replay needs. Mark unimplemented persistence as a requirement gap.
- [x] Review the full design, including changes to existing signatures and tests, before implementation planning.

## Task 5: Produce the first implementation prompt

**Files:** Create an implementation plan and a single Claude prompt only after the preceding contract design is approved.

- [x] Split implementation into reviewable increments: necessary record/port changes, pure verification/policy rules, loop orchestration with controlled ports, and subsequent real integration/persistence.
- [x] Define exact signatures, file ownership, stage outcomes and acceptance tests for the first increment; do not leave API choices to guesses.
- [x] Include the source-lifecycle brief and the approved contract document by accessible path in the prompt.
- [x] Require red/green evidence for new behavior, focused and architecture checks, and a handoff with remaining requirement gaps. Leave the diff uncommitted for review.

## Current next action

Tasks 1–4 approved by the owner on 2026-11-04. Task 5 planning is complete: use `2026-11-04-control-loop-budget-gate-plan.md` and its matching Claude prompt for the first increment only. Claude implements, Codex reviews, the owner commits. The control-loop design is not an authorization to implement all later increments at once.
