# File-backed State Inspector Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans sequentially in this session.

**Goal:** Inspect every current artifact and fixed gain input required by a StateIntegrityRequest, using the existing root resolver and WAV verifier.

**Architecture:** adapters.rendering.state_integrity implements StateIntegrityInspector. It reuses rendering.locations and rendering.integrity within the same adapter and returns only core integrity records. This is the next step toward reconciliation and the first connected loop.

**Tech Stack:** Existing Python, soundfile, NumPy, pytest, Ruff and uv.

**Spec:** core.records.integrity and core.ports.integrity, plus control-loop attempt contract sections 22 and 28–29.

## API and configuration

`FileStateIntegrityInspector(roots: Mapping[str, str], *, max_file_bytes: int, max_decoded_bytes: int)`

- Validate roots as a Mapping of nonblank string keys to nonblank string paths. Snapshot its items so later caller mutation cannot change configuration. Perform no filesystem access during construction.
- Validate limits as exact built-in int >= 1, matching the existing WAV verifier. Bounds apply separately to each file, not to the complete run.
- `inspect(self, request: StateIntegrityRequest) -> StateIntegrityReport` checks the request type, then processes request.required_artifacts once each, in order, using bindings by ID.

For each artifact, find its configured physical root from its logical binding. An unconfigured root yields UNCERTAIN with a precise explanation and no observed hash. Continue inspecting remaining artifacts.

Call resolve_artifact_location with the original artifact and explicit configured root, then check_resolved_wav_integrity with that location and configured limits. Keep the original artifact object in the result. Do not reconstruct artifacts with rewritten locations or reopen/re-read audio separately for hashing.

Successful checking yields VERIFIED, observed_sha256 from CheckedAudio's existing verified-hash attribute and an explanation of the successful checks. Inspect the exact existing attribute name before implementation. Samples remain local to the iteration and are never returned in core records.

## Expected error mapping

Catch only LocationResolutionError and IntegrityError around each artifact operation. Preserve the original code value and message in the result explanation. Set observed_sha256 to None for these failures, because existing exception objects do not expose captured hashes. Do not invent an observed hash from expected metadata.

| Error owner | Codes | Status |
| --- | --- | --- |
| LocationResolutionError | NOT_FOUND, UNSUPPORTED_PATH, OUTSIDE_ROOT, RESOLUTION_LOOP | FAILED |
| LocationResolutionError | INVALID_ROOT, NOT_READABLE, STALE_RESOLUTION | UNCERTAIN |
| IntegrityError | NOT_FOUND, UNSUPPORTED_PATH, NOT_REGULAR_FILE, HASH_MISMATCH, INVALID_WAV, METADATA_MISMATCH, NONFINITE_SAMPLES | FAILED |
| IntegrityError | NOT_READABLE, RESOURCE_LIMIT | UNCERTAIN |

FAILED means a concrete configured artifact check was rejected. UNCERTAIN means the inspection could not establish integrity with the supplied access/configuration/resources. It does not promise the artifact is intact or corrupt. Implement exhaustive enum handling so future added codes require an explicit mapping.

Continue after every expected error and return exactly one result per required artifact. Unexpected exceptions, including unrelated OSError, MemoryError and programming errors, propagate. The coordinator owns terminal recording of such failures. No retries, deletion, renames, writes or journal operations occur.

The report describes captured bytes and metadata during these calls. It does not establish race-free confinement, current file immutability, actual signal alignment, restoration or journal authority. Recovery workflow time gates remain coordinator work. This inspector performs one bounded-by-size sequential pass without a hard wall-clock guarantee.

## Owned paths and constraints

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve other work and source audio. Test WAVs are temporary numeric fixtures only. First real MVP audio is supplied by the owner from FL Studio, and the implementation is track-role agnostic.

- Add src/autonomous_mixing/adapters/rendering/state_integrity.py and tests/unit/test_file_state_integrity.py.
- Update tests/architecture/test_adapters.py with the new leaf's exact internal and core edges and its runtime isolation expectation. Retain all existing assertions and rule (c).
- Update overview in place and append research-log evidence and limits. Preserve requirement statuses and package initializers. No installs, dependencies, commits or pushes. Report any extra required path before editing.

## Task 1

- [ ] Record actual HEAD/status and inspect the existing integrity contract and verifier signatures. Preserve any staged integrity-contract work. Run baseline checks.
- [ ] Write meaningful red tests for constructor types and configuration snapshot, explicit roots, original artifact retention, relative and absolute locations, required output/fixed-input coverage and deduplication, observed hash on success, missing roots, every expected error mapping, continued complete coverage after failure, aggregate status and unexpected exception propagation.
- [ ] Use real tiny WAV files to cover verified payloads, missing files, changed hashes and metadata mismatch with independently correct hashes. Check file hashes before/after inspection. Use injected errors for inaccessible-platform cases and label them.
- [ ] Implement the inspector and exact architecture edge update. Run focused, core integrity and architecture checks. Validate every test report using validate_state_integrity_response.
- [ ] Document results and limits. Run full suite, Ruff lint/format, CLI help and uv lock --check using uv run --locked where available. Report omitted checks.
- [ ] Return signatures, changed paths, red/green evidence, native versus injected coverage, final verification and git status. Stop uncommitted for Codex review. Next task is reconciliation and then coordinator integration.
