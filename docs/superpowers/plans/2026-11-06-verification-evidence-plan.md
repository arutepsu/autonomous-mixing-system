# Verification Evidence Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Work sequentially in this session.

**Goal:** Match a criterion's declared measurement independently against each project state's observations.

**Architecture:** Immutable inputs and results in core.records.verification_evidence. Pure matching in core.verification.evidence. Reuse the current selector, observation and checkpoint contracts.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Owner-approved contract below, implementing the evidence portion of sections 19–20 of docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md.

## Records

All record fields are required and frozen. Use existing invariant helpers. Preserve input values and tuple order.

- SourceRequirement(kind: ArtifactKind, source_track_ids: tuple[TrackId, ...], render_spec: VersionRef, sample_rate_hz: int, channel_count: int, timeline_start_seconds: float, frame_count: int). Apply the corresponding AudioArtifact field constraints: distinct nonempty tracks, positive rate, one or two channels, finite nonnegative start, nonnegative frame count.
- MeasurementDefinition(selector: MetricSelector, unit: str, analysis_spec: VersionRef, analyzer: VersionRef, alignment: VersionRef, sources: tuple[SourceRequirement, ...]). Unit is nonblank. Sources are nonempty and ordered. The union of their contributor IDs must cover the selector's tracks. Contributor order within each source is matched exactly, as is the sequence of sources.
- StateEvidence(state: ProjectState, observations: tuple[Observation, ...], alignment: VersionRef). Observations may be empty. Reject repeated observation IDs, even if the records are identical, within this state bundle. Distinct IDs with the same metric and scope remain representable so the matcher can report ambiguity. Cross-state ID consistency belongs to the future multi-state assessment.
- MatchedEvidence(observation: Observation) requires a MeasuredScalar result and retains the original observation.
- EvidenceUnavailable(code: EvidenceUnavailableCode, explanation: str) requires nonblank text.
- EvidenceMatch = MatchedEvidence | EvidenceUnavailable.
- EvidenceUnavailableCode members and string values: MISSING/missing, AMBIGUOUS/ambiguous, SETTINGS_MISMATCH/settings_mismatch, INVALID_SOURCE/invalid_source, SUBJECT_MISMATCH/subject_mismatch, SOURCE_METADATA_MISMATCH/source_metadata_mismatch, UNAVAILABLE_RESULT/unavailable_result, UNIT_MISMATCH/unit_mismatch.

## Matching contract

`match_evidence(definition: MeasurementDefinition, evidence: StateEvidence) -> EvidenceMatch`. Check argument types. Apply the following precedence:

1. Filter observations by exact selector metric VersionRef and scope equality. Zero matches yields MISSING. More than one yields AMBIGUOUS before filtering by any other property, including availability.
2. Match the analysis spec, analyzer and caller-declared alignment reference exactly. Mismatch yields SETTINGS_MISMATCH.
3. Call validate_observation_sources with the bundle state's checkpoint artifacts, checkpoint and snapshot. Convert only its ValueError to INVALID_SOURCE. Resolve the observation's ordered artifact IDs from that same checkpoint. Unexpected exceptions propagate.
4. Compare the ordered source list to the definition. Different cardinality, kind or contributor tuple yields SUBJECT_MISMATCH. Once all subjects match, differing render version, sample rate, channels, start or frame count yields SOURCE_METADATA_MISMATCH. Use exact comparisons. Do not compare artifact IDs, paths, hashes or production revisions across states.
5. An Unavailable result yields UNAVAILABLE_RESULT with its reason. For a scalar, require exact unit equality or return UNIT_MISMATCH. Return the original observation in MatchedEvidence, including a measured zero.

The alignment reference is supplied by the caller and records a declaration. The matcher establishes matching declarations and metadata coverage. Physical alignment, file integrity, project/run binding across states, measurement-method validity and threshold justification remain separate. Definitions are supplied explicitly. The future multi-state verifier must bind each criterion to its definition and check condition units, baseline/candidate project and observation-ID consistency. This task calculates no conditions or verdicts.

## Scope

Read CLAUDE.md, applicable AGENTS.md and project references. Preserve existing work and original audio. Use labelled metadata fixtures and existing tools. Work sequentially. No dependencies, installs, commits or pushes.

- Add src/autonomous_mixing/core/records/verification_evidence.py and src/autonomous_mixing/core/verification/evidence.py.
- Add tests/unit/test_verification_evidence.py. Update tests/architecture/test_core_records.py with the new inventory and exact imports used from artifacts, identifiers, invariants, observation, project and verification. Update tests/architecture/test_core_layers.py with verification.evidence and match_evidence. Keep existing rules and docstring-only initializers.
- Update docs/architecture/overview.md in place and append evidence to docs/research/research-log.md. Keep requirement statuses. Report any additional path needed before editing it.

## Review focus

- Same track coverage can describe a stem pair or a summed mix. Source count, kind and ordered contributors distinguish them.
- Two exact metric/scope matches are ambiguous even when one would otherwise be usable.
- Retained older-production members and fresh candidate IDs can match. Superseded nonmembers cannot.
- A requested scope must be covered by each source's timeline and Nyquist bounds through the existing validator.
- Exact metadata and declared alignment agreement leave physical alignment unresolved.

## Task 1

- [ ] Capture branch, HEAD and working tree. Read the current record and architecture contracts. Run baseline tests and tool checks.
- [ ] Write and run focused red tests for record invariants, every failure code and precedence, zero versus Unavailable, same-subject replacement IDs, retained sources, superseded sources, reordered stems, summed mix, exact metadata differences, mismatched alignment/settings/units and ambiguous matches. Prove the original observation is returned on success.
- [ ] Implement records and matcher. Update inventories. Run focused and architecture tests.
- [ ] Update overview and append research-log evidence and limits. Verify full suite, Ruff lint/format, CLI help and uv lock --check. Use uv run --locked where available and report omitted checks.
- [ ] Return exact signatures, changed paths, red/green evidence, final verification, deviations and git status. Stop uncommitted for Codex review.
