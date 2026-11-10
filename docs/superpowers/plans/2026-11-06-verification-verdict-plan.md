# Verification Verdict Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Work sequentially in this session.

**Goal:** Compute INITIAL and ATTEMPT verification results from declared measurement definitions and state evidence.

**Architecture:** Immutable assessment inputs in core.records.verification_assessment. Pure computation in core.verification.assessment. Reuse match_evidence and validate_verification_references.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Approved verdict rules in docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md sections 19–21, with the input contract below. Requires the evidence matcher.

## Assessment contract

Define frozen records with required fields and no defaults:

- ConditionMeasurement(condition_id: ConditionId, definition: MeasurementDefinition).
- VerificationAssessment(result_id: VerificationResultId, mode: AssessmentMode, criteria: AcceptanceCriteria, measurements: tuple[ConditionMeasurement, ...], assessed: StateEvidence, before: StateEvidence | None, initial: StateEvidence | None).

Validate types, exact one-to-one condition ID coverage (reject duplicates, missing and extra IDs), exact selector equality and unit equality with each condition's threshold, minimum_delta or maximum_absolute_delta. Validate bindings even for deferred INITIAL checks. Measurement tuple order is independent of output check order.

In INITIAL, assessed is the initial state and both baseline fields must be None. In ATTEMPT, assessed is the candidate, before is required and its revision must differ from assessed. Require initial if any delta guard names INITIAL. If supplied, initial must differ in revision from assessed and may equal before. Require every supplied bundle to share the same project ID. Two bundles naming the same revision must carry equal ProjectState records, though observations may differ. Across all supplied bundles, repeated observation IDs must carry identical Observation contents. Reject violations with ValueError. These checks do not establish actual run membership or chronological order.

`assess_verification(request: VerificationAssessment) -> VerificationResult` checks its argument type, produces one CheckResult for every condition in targets/improvements/guards order, constructs the result with the supplied ID and criteria identity, and calls validate_verification_references before returning.

## Checks

- INITIAL defers every improvement and delta guard as NOT_APPLICABLE with explanation and empty evidence IDs. Match no evidence for these checks.
- For applicable checks, match assessed evidence with the bound definition. For comparisons also match the appropriate baseline with that same definition. Improvement uses before. Delta guard uses before for ATTEMPT or initial for INITIAL baseline references.
- If either operand is unavailable, emit UNAVAILABLE with its matcher code, explanation and operand role. Include any successfully matched operand's ID. If both are unavailable, explain both. Do not use a failed match as a numeric operand.
- Numeric checks cite assessed ID then baseline ID if used, deduplicating an identical ID while preserving order. Explanations identify the comparison and its values or the reason a numeric difference was unavailable.
- Target and absolute guard apply the exact LT, LE, GT or GE operation to assessed value versus threshold. Improvement uses before minus assessed for LOWER or assessed minus before for HIGHER, with PASS at or above minimum_delta. Delta guard passes when abs(assessed minus baseline) is at or below maximum_absolute_delta.
- Preserve stored scalar arithmetic. No rounding, epsilon, clipping, rescaling or implicit conversion. For differences, catch only OverflowError from arithmetic or representability checking, and report a nonfinite or unrepresentable difference as UNAVAILABLE with both matched IDs. Unexpected errors propagate. Do not catch all ValueError from comparison code.

## Verdict

Compute target status first. Any target FAIL yields UNMET. Otherwise any target UNAVAILABLE yields UNKNOWN. Otherwise SATISFIED.

INITIAL precedence: any applicable FAIL → REJECTED. Otherwise any applicable UNAVAILABLE → INCONCLUSIVE. Otherwise ACCEPTED.

ATTEMPT precedence:

1. Any guard FAIL → REJECTED.
2. UNMET targets and any improvement FAIL → REJECTED.
3. Any UNAVAILABLE → INCONCLUSIVE.
4. Otherwise ACCEPTED. This includes SATISFIED targets with an available failing improvement, and UNMET targets with every improvement and guard passing.

Compute every check even when an early condition determines the verdict. The result is consumed by a future loop. It carries no state transition, restoration or acceptance publication.

## Owned paths and constraints

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve existing work and original audio. Use existing tools and labelled metadata fixtures. No new dependencies, installs, commits or pushes.

- Add src/autonomous_mixing/core/records/verification_assessment.py and src/autonomous_mixing/core/verification/assessment.py.
- Add tests/unit/test_verification_assessment.py. Add the two record names and exact used owner edges to tests/architecture/test_core_records.py. Add verification.assessment with assess_verification to tests/architecture/test_core_layers.py.
- Update docs/architecture/overview.md in place and append docs/research/research-log.md. Keep requirement statuses and document remaining loop and scientific gaps. Report any additional file needed before editing it.

## Task 1

- [ ] Record baseline HEAD/status and checks. Read existing criteria, evidence matcher and consistency validator.
- [ ] Write and run focused red tests for input bindings, cross-project/revision and observation-ID conflicts, initial-versus-before baseline selection, all operators and exact boundaries, both improvement and delta directions, overflowing differences, unavailable operands and stable evidence order. Include identical observation reuse and distinct source IDs on corresponding states.
- [ ] Cover verdict precedence using an independent table or parameterized cases, including UNKNOWN targets with failing improvement, guard failure with missing evidence, UNMET plus failed improvement with missing evidence, SATISFIED plus unavailable improvement, and INITIAL deferred checks. Confirm every check is recorded in configuration order.
- [ ] Implement the records and assessment. Update the inventories and run focused and architecture tests.
- [ ] Document and verify full suite, lint, format, CLI help and uv lock --check. Use uv run --locked where available and report omitted checks. Inspect final diff and leave uncommitted with exact signatures, red/green evidence, verification and gaps.
