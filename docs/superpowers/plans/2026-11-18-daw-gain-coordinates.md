# DAW Gain Coordinates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Preserve unrelated work and leave changes uncommitted for owner/Codex review.

**Goal:** Define truthful DAW parameter state and deterministically translate an absolute gain action into an absolute fader target and relative bridge delta, without I/O.

**Architecture:** Frozen core records describe application-to-DAW coordinates independently of fixed-input audio processing. A pure admission-stage helper validates references and resolves coordinates; it does not grant permission or dispatch. The FL transport, recovery, manual exports and coordinator integration follow in separate plans.

**Tech Stack:** Existing Python, standard library, pytest, uv and Ruff. No new dependency.

**Spec:** [Approved design](../specs/2026-11-18-fl-studio-action-adapter-design.md).

## Global Constraints

- Implement only this first contract slice. No live FL calls, installed-script edits, MIDI, saving or exports.
- Preserve `SetTrackGain`, `TrackGainState`, `RenderPlan`, Renderer and RunRenderer definitions and semantics.
- Absolute mapping: `target_fader_db = baseline_fader.db + (action.operation.value_db - baseline_gain_db)`.
- Relative delta: `target_fader_db - observed_fader.db`. This value is not itself authorization to execute.
- Existing ADR-009 remains unchanged and FR-14 remains withdrawn. These are preparatory contracts, not adoption of FL as the runtime renderer.
- Records import only records/standard library. Admission imports only records/ports/standard library. All initializers remain docstring-only.
- Use `uv run --locked` for checks. Preserve existing uncommitted files; do not stage, commit or push.

## Review Focus

1. Nonzero baseline: never equate application gain with the physical fader dB.
2. Sequential absolute actions: resolve against the original binding, not the preceding action as a new baseline.
3. External drift: reject stale state instead of silently treating observed drift as selected state.
4. Malformed numbers: bool, NaN, infinity and arithmetic overflow cannot produce a usable resolution.
5. Apparent no-change: report the exact delta, including zero; this helper never sends a cut or bypasses admission.

## File map

- New `src/autonomous_mixing/core/records/daw.py`: four immutable metadata records below.
- New `src/autonomous_mixing/core/admission/daw.py`: one pure resolver.
- New `tests/unit/test_daw_gain_coordinates.py`: constructors, references and coordinate arithmetic.
- Modify `tests/architecture/test_core_records.py`: exact `daw` inventory and edges only.
- Modify `tests/architecture/test_core_layers.py`: `admission.daw` inventory addition only.
- Modify overview and append research log with precise implemented limits.

## Task 1: Frozen DAW coordinate records

**Produces** these exact names, constructors and field order in `core.records.daw`:

```python
DawFaderReading(normalized: float, db: float)
DawTrackBinding(reference: VersionRef, project_id: ProjectId, track_id: TrackId,
                project_title: str, insert_index: int, insert_name: str,
                baseline_gain_db: float, baseline_fader: DawFaderReading)
DawTrackState(binding: DawTrackBinding, revision: Revision,
              selected_gain_db: float, confirmed_fader: DawFaderReading)
DawGainResolution(action: Action, state: DawTrackState,
                  observed_fader: DawFaderReading,
                  target_fader_db: float, delta_db: float)
```

`DawFaderReading`: finite real numeric values, explicitly reject bool; normalized inclusive [0,1]. Zero normalized is structurally valid; the FL calibrated profile later rejects unsupported positions. Do not put the calibration curve into core.

`DawTrackBinding`: typed identifiers/reference; nonblank title/name using existing require_text convention; insert_index exact built-in int >=1 (master excluded); finite non-bool baseline gain; exact record type checks consistent with existing require_instance. It is metadata, not proof that a file is open. No FL build default, implicit Bass name or inferred track identity.

`DawTrackState`: typed binding/revision/reading and finite non-bool selected gain. Do not assert that fader dB equals baseline arithmetic: prior writes can land within tolerance and measured readings must remain representable.

`DawGainResolution`: check constituent types, action project/track/revision against state; finite non-bool target/delta; exact equality to the two mapping formulas. Reject non-finite intermediate results with ValueError. It remains a parameter mapping, never an admission token or an execution receipt.

Allowed records edges: `identifiers`, `invariants`, `actions`. No re-exports, aliases or extra public names.

- [ ] Write constructor and resolution tests first. Cover invalid types, all fields, range endpoints, bool, nonfinite values, mismatched action references and inconsistent derived fields; read-only/frozen behavior follows repository conventions.
- [ ] Run `uv run --locked pytest tests/unit/test_daw_gain_coordinates.py -q`; record collection failure for missing module before implementing.
- [ ] Implement the four records, using private helpers for repeated non-bool finite checks and mapping validation.
- [ ] Add the exact architecture inventory and run the new tests plus `tests/architecture/test_core_records.py`.
- [ ] Leave the independently reviewable change uncommitted.

## Task 2: Pure resolver with explicit stale-reading tolerances

**Consumes:** Task 1 records and existing `Action` / `SetTrackGain`.

**Produces** in `core.admission.daw`:

```python
def resolve_daw_gain(action: Action, *, state: DawTrackState,
                     observed_fader: DawFaderReading,
                     normalized_tolerance: float,
                     db_tolerance: float) -> DawGainResolution: ...
```

Validate all input types and finite, non-bool tolerances >=0. Validate project, track and expected revision against the state before arithmetic. Require the observed readings to be within both tolerances of `state.confirmed_fader`, inclusive; otherwise ValueError. Compute target from the immutable baseline binding and delta from the fresh observed reading, then construct `DawGainResolution`. No tolerances/defaults inferred from FL, no target clamp, curve conversion, gain permission decision, clock, retry, reservation or I/O. Callers must still admit the action and check runtime capability before dispatch.

Pin arithmetic with these cases (ProjectId/TrackId/Revision/VersionRef fixtures follow existing constructors):

| baseline app gain / fader dB | selected app gain / confirmed dB | action absolute | observed dB | target dB | delta dB |
| --- | --- | --- | --- | --- | --- |
| +2 / −4 | +2 / −4 | +1 | −4 | −5 | −1 |
| +2 / −4 | +1 / −5 | 0 | −5 | −6 | −1 |
| +2 / −4 | +1 / −4.999 | 0 | −4.999 | −6 | −1.001 |
| 0 / 0 | −2 / −2 | −2 | −2 | −2 | 0 |
| 0 / 0 | −2 / −2 | −1 | −2 | −1 | +1 |

Use exactly representable values for equality/boundary tests; use pytest.approx only for the explicit decimal −1.001 example. Positive/zero deltas are legitimate mappings, not supported bridge writes. The later profile gate owns that refusal.

- [ ] Write failing tests for the table, each reference mismatch, stale normalized-only and dB-only observations, exact tolerance edges, zero/invalid tolerances, overflow, preserved inputs, and unchanged baseline across sequential states.
- [ ] Run the tests and record the missing-function failure.
- [ ] Implement `resolve_daw_gain`; add `admission.daw` with only `resolve_daw_gain` to the layer inventory.
- [ ] Run the focused tests and architecture/public-contract suites.
- [ ] Mutation probes, reverting each byte-identically: replace baseline target with current reading + absolute action; silently accept stale reading; omit revision comparison. Each must fail a permanent test.
- [ ] Leave uncommitted for review.

## Task 3: Documentation and final verification

- [ ] Add a concise overview note: coordinates implemented; no admission enforcement, FL transport adapter, candidate artifacts, runtime recovery or coordinator use supplied by this slice.
- [ ] Append a research-log entry with baseline HEAD, actual red/green and mutation evidence, decisions and remaining gaps. Do not rewrite earlier entries or change requirement statuses.
- [ ] Run `uv run --locked pytest tests/unit/test_daw_gain_coordinates.py tests/architecture tests/unit/test_public_contracts.py -q -W error`.
- [ ] Run `uv run --locked pytest -q -rs`, `uv run --locked ruff check .`, `uv run --locked ruff format --check src tests`, `uv run --locked autonomous-mixing --help`, and `uv lock --check`; record actual outcomes and pre-existing unrelated failures separately.
- [ ] Inspect diff: no existing behavior assertions removed or weakened, no dependency/initializer/bridge-script changes. Report exact public signatures and git status; leave uncommitted.

## Subsequent plans (not executable tasks in this slice)

1. Proposed adoption ADR plus requirement/scope update for owner review; DAW inspect/apply/result ports and an adapter-local calibrated profile; extract reviewed bridge protocol/transport into runtime ownership without importing research scripts. Preserve original trial reproducibility.
2. Exact-target recovery contract, separate authorization/budget, implementation and live restoration tests. Do not repurpose calibration commands as production rollback.
3. Pending export request/continuation and verified artifact import; specify DAW routing/dependency evidence and analysis conversion profile.
4. Coordinator integration with explicit DAW state alongside audio authority, cancellation/deadline checks, recovery reservations, trace events and acceptance. No fader-only candidate-ready result.

These plans need exact interfaces and review against the approved design before implementation. This separation is intentional: the synchronous existing renderer and fixed-input processing state cannot silently stand in for the DAW workflow.

## Self-review

This plan implements only the design's coordinate/state prerequisite. All five review-focus cases have Task 1/2 checks. Public types used by Task 2 are defined in Task 1. Future integration work is explicitly excluded rather than assigned undefined interfaces. No scientific metric or threshold is introduced.
