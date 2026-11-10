# Verification Routing Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Work sequentially in this session.

**Goal:** Map a computed verification result to the coordinator's next obligation.

**Architecture:** Stable route enum in core.records.verification_routing. Pure mapping in core.loop.verification_routing. The verifier computes verdicts. The coordinator will enforce the route's acceptance, recovery and budget prerequisites.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Control-loop attempt contract sections 20–22 and 27, especially INITIAL_ASSESSMENT_INCONCLUSIVE and VERIFICATION_INCONCLUSIVE. The section 27 terminal policy governs the distinction between rejected and inconclusive assessments.

## Contract

Define VerificationRoute with the following members and exact lowercase string values (lowercase spelling of the member).

| Mode | Verdict | Target status | Route |
| --- | --- | --- | --- |
| INITIAL | ACCEPTED | SATISFIED | FINISH_INITIAL_SUCCESS |
| INITIAL | REJECTED | Any structurally consistent status | CHECK_BASELINE_READINESS |
| INITIAL | INCONCLUSIVE | Any structurally consistent status | FINISH_INITIAL_INCONCLUSIVE |
| ATTEMPT | ACCEPTED | SATISFIED | COMMIT_THEN_FINISH |
| ATTEMPT | ACCEPTED | UNMET | COMMIT_THEN_ITERATE |
| ATTEMPT | REJECTED | Any structurally consistent status | RESTORE_THEN_CONSIDER_ITERATION |
| ATTEMPT | INCONCLUSIVE | Any structurally consistent status | RECONCILE_THEN_FINISH_INCONCLUSIVE |

`route_verification(result: VerificationResult, criteria: AcceptanceCriteria) -> VerificationRoute` checks both types with require_instance and calls validate_verification_references before routing. Propagate structural errors. Use the supplied verdict from assess_verification. Do not duplicate its computation or claim the consistency validator authenticates an arbitrary result's numerical verdict.

FINISH_INITIAL_SUCCESS describes the intended success route, subject to the coordinator's known accepted reference and finalization checks. CHECK_BASELINE_READINESS requires later checks before correction. FINISH_INITIAL_INCONCLUSIVE maps to the future INITIAL_ASSESSMENT_INCONCLUSIVE termination. Commit routes require confirmed acceptance before success or iteration. Restoration after rejection must be verified before considering another budgeted decision. Reconciliation after inconclusiveness precedes termination under the existing first-version policy. These names describe future obligations. The function itself performs no transitions, effects or budget charges.

## Scope

Read CLAUDE.md, applicable AGENTS.md and the required references. Preserve existing work. Use metadata fixtures and existing tools. Keep all current architecture rules and package initializers. Leave uncommitted.

- Add src/autonomous_mixing/core/records/verification_routing.py and src/autonomous_mixing/core/loop/verification_routing.py.
- Add tests/unit/test_verification_routing.py. Add VerificationRoute and its exact import edges (stdlib only, empty records edge set) to tests/architecture/test_core_records.py. Add loop.verification_routing with route_verification to tests/architecture/test_core_layers.py.
- Update docs/architecture/overview.md in place and append docs/research/research-log.md. Keep requirement statuses. Report any additional path needed before editing.

## Task 1

- [ ] Record baseline HEAD/status. Read current verdict computation, consistency validator and section 27.
- [ ] Write and run red tests for all seven routes, exact enum values, malformed types and criteria/reference mismatches. Include INITIAL inconclusive versus rejected, and ATTEMPT inconclusive versus rejected even when both target statuses agree. Use coherent verification results. Include evidence that the result and criteria remain unchanged.
- [ ] Implement the mapping and inventories. Run focused, verification and architecture checks.
- [ ] Document the standalone route contract. Run the full suite, Ruff lint/format, CLI help and uv lock --check. Prefer uv run --locked and report omitted checks. Review final scope and report signatures, verification and git status. No commits, pushes, dependency changes or tool installs.
