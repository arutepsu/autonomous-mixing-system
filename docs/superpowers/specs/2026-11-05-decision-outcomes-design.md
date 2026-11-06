# Decision outcomes and proposal references — increment 2E

Status: schema approved by the owner on 2026-11-05; this document specifies that approved schema. Implementation scope is limited by the accompanying plan.

## Intent and approach

Implement the record/reference portion of sections 12–15 of the approved control-loop attempt contract. Preserve OBSERVE → DIAGNOSE → PLAN/DECIDE → evidence/context gate → ACT → verification → STOP or ITERATE/ROLLBACK. The owner chose two increments: these explicit outcomes and structured references first; deterministic evidence/context sufficiency and routing next. Combining them would obscure the distinction between a structurally valid response and permission to act.

Diagnostic uncertainty concerns whether a problem is plausible. Action justification concerns why a particular operation is appropriate. Neither proves perceptual correctness, and no calibrated confidence score is introduced.

## Exact records in core.records.decision

All records remain frozen, preserve supplied values, and follow existing ValueError validation conventions (omitted required constructor arguments retain TypeError).

- `ActionJustification(reasoning: str, uncertainty: str, assumptions: tuple[str, ...], limitations: tuple[str, ...])`: all fields required. Reasoning and uncertainty must be non-blank text. Assumptions and limitations must be tuples of non-blank, distinct strings; empty tuples are valid explicit declarations. No numeric confidence, sufficiency flag, default uncertainty or inferred diagnostic strength.
- `ProposeAction(action: Action, expected_effect: str, candidate_ids: tuple[ProblemCandidateId, ...], justification: ActionJustification)`: retain first two fields and their checks; append two required fields without defaults. Candidate IDs must be a non-empty tuple of correctly typed, distinct IDs. A constructor cannot establish that they exist in a particular diagnosis report.
- `NoChange(explanation: str)`: required non-blank text. No Action, verification, success flag or default explanation.
- `Decision.outcome: ProposeAction | NoChange | Abstain`: retain all other fields and existing action project/revision and nonempty action-evidence checks. A NoChange decision may have empty or nonempty evidence; supplied evidence must still pass existing reference validation. NoChange is distinct from NoChangeFeedback, which records a previous call's summary.
- Retain Abstain's signature and enum values. MISSING_CONTEXT now requires at least one item in requested_context; existing tuple, non-blank and uniqueness checks remain. Other reasons retain their existing allowance for empty or nonempty requested_context. Do not silently change or canonicalize the provider's outcome.

Missing-context strings are intended to be actionable questions identifying what is missing and why it matters. Structural code checks presence and nonblank text, not punctuation or natural-language quality; it cannot prove a question's usefulness. No answers are guessed.

## Reference checks and ownership

Extend `core.ports.decision.validate_decision_response(request, response)` after its current checks. For a ProposeAction, every candidate_id must identify a candidate in request.diagnosis. For every selected candidate, all its evidence_ids must occur in decision.evidence_ids (set containment). Extra decision evidence remains allowed under existing observation reference checks. Do not require equality of these sets or allow unselected candidates to impose their evidence requirements.

Retain `validate_decision_references(decision, snapshot, context, observations)` signature and existing responsibilities. It lacks a diagnosis report, so it must not pretend to validate candidate existence. The request-aware response validator owns the new checks; admission already invokes it, so malformed candidate references are rejected before policy checks using the existing INVALID_REFERENCES route. This is an intentional structural contract change, not a new semantic evidence gate.

NoChange and valid Abstain responses pass structural response validation. Existing direct admission rejects them as NOT_AN_ACTION when prior references and policy-project checks pass; preserve the established first-failure order. No new rejection code is required. Future loop routing will avoid presenting non-actions as executions.

## Explicit non-goals

Do not reject candidates merely for insufficient/Unavailable evidence in this increment; that belongs to the next gate. Do not infer that an action target must equal a candidate's track scope without a separately approved semantic rule. Do not enforce advertised bounds, protections or scope through this new reference check; existing admission behavior remains in force. No automatic success, rendering, history projection, termination, provider or persistence is implemented.

Missing context will ultimately end the run with recorded questions and preserve its actual last accepted state; updated context starts a new run. This increment makes that decision representable and rejects empty questions, but does not implement the loop/driver behavior.

## Acceptance and migration

Tests cover constructor types, required fields, immutability, nonblank/unique tuple entries, explicit empty assumptions/limitations, all outcome variants, missing-context questions, existing enum values, candidate existence and complete evidence inclusion for one/multiple selected candidates. Extra evidence is allowed, unselected candidates are ignored, and NoChange is not treated as success or a gain operation. Direct admission of valid NoChange is NOT_AN_ACTION.

Migrate existing proposals explicitly. Tests involving request-aware response validation need labelled diagnosis test doubles with the referenced candidates and actual cited observation IDs. Do not populate real detector methods or imply scientific adequacy. Preserve old assertions and negative-case intent. Tests of MISSING_CONTEXT without questions must become explicit rejection coverage; positive enum-coverage tests supply questions for that member.

Records.decision gains only ProblemCandidateId from its already allowed identifiers owner. Add the two new public classes to the records inventory. No dependency broadening, package exports or signature changes to ports are required. Document the intentional ProposeAction constructor break, Decision union extension and stricter MISSING_CONTEXT construction. No requirement is completed.
