# State Integrity Contract Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans sequentially in this session.

**Goal:** Define complete state-integrity requests and truthful per-artifact results for future initialization and recovery checks.

**Architecture:** Immutable records in core.records.integrity. StateIntegrityInspector Protocol and response binding check in core.ports.integrity. File inspection follows in a separate adapter task.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Control-loop attempt contract sections 22, 28 and 29. Integrity evidence is independent of journal authority. The coordinator chooses which state to inspect. First real music comes from owner-supplied FL Studio exports, and all roles remain generic.

## Required artifacts and bindings

The required set is every state.audio artifact, followed by every processing.track_gains input_artifact not already present, preserving their existing order. Deduplicate by artifact ID, retaining the exact metadata. ProjectState already enforces metadata equality when a fixed input also appears in current audio. Retained older production revisions are valid.

Define frozen records with required fields:

- ArtifactRootBinding(artifact_id: ArtifactId, root_key: str). Root key is nonblank and logical. Physical root paths are adapter configuration supplied later.
- StateIntegrityRequest(state: ProjectState, bindings: tuple[ArtifactRootBinding, ...]). Bindings must name every required artifact exactly once, with no extras. Binding tuple order is unrestricted. Validate types and duplicates. A read-only required_artifacts property returns the ordered artifact tuple described above.
- IntegrityStatus enum has VERIFIED="verified", FAILED="failed", UNCERTAIN="uncertain".
- ArtifactIntegrityResult(artifact: AudioArtifact, status: IntegrityStatus, observed_sha256: str | None, explanation: str). Explanation is nonblank. A supplied hash must be lowercase 64-character SHA-256. VERIFIED requires a supplied hash exactly equal to artifact.content_hash. FAILED and UNCERTAIN may carry a hash or None. A matching hash alone does not require VERIFIED because format or metadata verification may have failed.
- StateIntegrityReport(request: StateIntegrityRequest, artifacts: tuple[ArtifactIntegrityResult, ...]). Require one result for every required artifact, in required_artifacts order, with exactly equal artifact metadata. All results must be present even after a failure. The future adapter can mark uninspected files UNCERTAIN with a reason.

StateIntegrityReport.status is a derived property: FAILED if any item failed, otherwise UNCERTAIN if any item is uncertain, otherwise VERIFIED. Require a nonempty required set; ProjectState's checkpoint already supplies it. No separate supplied aggregate flag can contradict the individual results.

VERIFIED records the inspector's claim of successful hash, format and metadata inspection. Record construction checks structure, not the truth of external inspection. The existing WAV verifier will supply the actual evidence in the next task. These reports do not prove restoration or publication of a selected reference.

## Port

StateIntegrityInspector(Protocol), without runtime_checkable:

`inspect(self, request: StateIntegrityRequest) -> StateIntegrityReport`

`validate_state_integrity_response(request: StateIntegrityRequest, response: StateIntegrityReport) -> None` checks both types and response.request == request, binding complete state metadata and the exact root-binding tuple. Records already enforce complete result coverage.

Expected inspection failures and uncertainty are recorded per artifact. Unexpected implementation errors propagate. The coordinator owns recovery/time budgets and selects the appropriate candidate or previous state based on journal authority. The inspector reads the supplied request and never chooses an accepted state.

## Owned paths

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve existing work, original audio and generic track identities. Use labelled metadata fixtures. No installs, dependencies, commits or pushes.

- Add src/autonomous_mixing/core/records/integrity.py and src/autonomous_mixing/core/ports/integrity.py.
- Add tests/unit/test_state_integrity_contracts.py.
- Update tests/architecture/test_core_records.py with integrity's exact owners artifacts, identifiers, invariants and project, and the five public record/enum names. Update tests/architecture/test_core_layers.py with the Protocol and response validator. Preserve current graph rules and docstring-only initializers.
- Update overview in place and append research-log evidence and limits. Preserve requirement statuses. Report any additional required path before editing.

## Task 1

- [ ] Record baseline HEAD/status and inspect existing ProjectState, checkpoint, fixed-input, root resolution and WAV verification contracts. Run baseline checks.
- [ ] Write meaningful red tests for ordered deduplication, superseded fixed inputs retained in required set, older-production artifacts, binding completeness/duplicates/extras, immutable records, wrong types, hash shape and VERIFIED hash equality, matching hash with FAILED metadata, full report coverage/order/metadata, aggregate precedence and exact response/request binding.
- [ ] Implement records, Protocol and validator. Add exact inventories and run focused, acceptance and architecture tests.
- [ ] Update docs and verify full suite, Ruff lint/format, CLI help and uv lock --check, using uv run --locked where available. Report omitted checks.
- [ ] Review file scope and return signatures, red/green evidence, verification, deviations, gaps and git status. Stop uncommitted for Codex review.
