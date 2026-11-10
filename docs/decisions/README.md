# Architectural decision records

One file per decision, named `NNNN-short-title.md`. Accepted ADRs are not edited; a change is a new ADR that supersedes the old one, and both are noted in this index ([ADR-003](0003-documentation-governance.md), [ADR-007](0007-docs-folder-structure.md)). File paths mentioned inside an ADR refer to the repository layout at the time it was written.

| ADR | Title | Date | Status |
| --- | --- | --- | --- |
| [001](0001-offline-python-workflow.md) | Offline Python WAV workflow, Claude for action selection, modular monolith | 2026-10-14 | Accepted |
| [002](0002-uv-dependency-management.md) | uv dependency management | 2026-10-14 | Accepted |
| [003](0003-documentation-governance.md) | Documentation governance | 2026-10-14 | Accepted; storage of ADRs refined by ADR-007 |
| [004](0004-single-agent-deterministic-orchestration.md) | Single-agent, deterministic orchestration | 2026-10-14 | Accepted |
| [005](0005-system-oriented-naming.md) | System-oriented naming | 2026-10-15 | Accepted |
| [006](0006-research-first-development-policy.md) | Research-first development policy | 2026-10-15 | Accepted; point 6 clarified by ADR-008 |
| [007](0007-docs-folder-structure.md) | Documentation folder structure and one file per ADR | 2026-10-15 | Accepted |
| [008](0008-controlled-defect-injection.md) | Controlled defect injection into real stems | 2026-10-15 | Accepted; recording location refined by ADR-010 |
| [009](0009-agent-team-implementation-baseline.md) | Development agent team follows the offline Python/WAV baseline | 2026-10-15 | Accepted; hidden-reference item closed by ADR-010 |
| [010](0010-hidden-reference-boundary.md) | Hidden-reference boundary between runtime and research evaluation | 2026-10-15 | Accepted |
| [011](0011-domain-foundation-boundary.md) | Dependency-free domain records for projects, context and observations | 2026-10-19 | Proposed |
| [012](0012-typed-action-proposals-and-decisions.md) | Typed action proposals and single-outcome decisions | 2026-10-19 | Proposed |
| [013](0013-validation-criteria-and-result-records.md) | Validation criteria and result records without an evaluator | 2026-10-19 | Proposed |
| [014](0014-run-lifecycle-and-budget-records.md) | Run lifecycle and budget records without a state machine | 2026-10-19 | Proposed |
| [015](0015-correction-attempt-and-artifact-checkpoint-records.md) | Correction-attempt and artifact-checkpoint records | 2026-10-19 | Proposed |
| [016](0016-application-observation-and-decision-contracts.md) | Application observation and decision contracts | 2026-10-20 | Proposed |
| [017](0017-domain-component-packages.md) | Domain organized into component packages with enforced dependencies | 2026-10-20 | Proposed; superseded by ADR-027 |
| [018](0018-explicit-gain-action-admission-policy.md) | Explicit gain-action admission policy | 2026-10-20 | Proposed |
| [019](0019-fixed-input-gain-and-composite-project-state.md) | Fixed-input gain semantics and composite project state | 2026-10-21 | Proposed |
| [020](0020-execution-contracts-and-metadata-preflight.md) | Offline execution contracts and deterministic metadata preflight | 2026-10-21 | Proposed |
| [021](0021-processing-spec.md) | Explicit processing specification carried through execution contracts | 2026-10-21 | Proposed |
| [022](0022-application-component-packages-and-infrastructure-boundaries.md) | Application component packages and enforced application/infrastructure import boundaries | 2026-10-22 | Proposed; superseded by ADR-027 |
| [023](0023-explicit-root-artifact-location-resolution.md) | Explicit-root, read-only artifact-location resolution with a separate provenance record | 2026-11-02 | Proposed |
| [024](0024-fresh-output-workspace-allocation.md) | Fresh output workspace allocation in a separate infrastructure component | 2026-11-02 | Proposed |
| [025](0025-exclusive-wav-output.md) | Exclusive WAV/DOUBLE output in a separate infrastructure component, with read-only workspace revalidation | 2026-11-03 | Proposed |
| [026](0026-gain-mix-processor-adapter.md) | Gain/full-mix processor adapter bound to a pre-allocated workspace | 2026-11-03 | Accepted |
| [027](0027-hexagonal-control-loop-architecture.md) | Hexagonal architecture around a MAPE-K control loop, with immutable records | 2026-11-03 | Accepted; supersedes ADR-017 and ADR-022 |
| [028](0028-shared-read-only-audio-access.md) | Shared read-only audio access component with exact client edges | 2026-11-10 | Proposed |

## Template

```markdown
# ADR-NNN — title (YYYY-MM-DD)
- Decision:
- Source:
- Rationale:
- Consequences:
- Supersedes: (optional)
```
