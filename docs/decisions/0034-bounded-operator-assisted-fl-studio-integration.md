# ADR-034 — Bounded, operator-assisted FL Studio runtime integration (2026-11-18)

- **Status:** Proposed, for owner and Codex review. It records the owner's decision on D15 (2026-11-18) and the scope it authorizes. It takes effect for implementation planning only. Each implementation increment of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md) needs its own approval, and installation and live FL Studio calls need separate authorization.
- **Supersedes, in part, [ADR-009](0009-agent-team-implementation-baseline.md)** (Accepted; the file is unchanged). Only the clause *"FL Studio integration is deferred and optional: it is not scheduled, and it is investigated only when the owner explicitly assigns it; adopting it would require a new requirement and ADR"* is superseded, and only for the bounded scope below. ADR-009's own condition is met: the owner assigned the work, and this ADR plus FR-19 and FR-20 are the new ADR and requirements it asked for. Everything else in ADR-009 stands, including the offline Python/WAV implementation baseline for the development roles.
- **Unchanged:**
  - [ADR-001](0001-offline-python-workflow.md): offline Python WAV workflow first. The offline path remains the primary, complete implementation, and the DAW path is an optional second execution path.
  - [ADR-004](0004-single-agent-deterministic-orchestration.md): the agent only proposes; the deterministic coordinator admits, executes, verifies and accepts.
  - [ADR-010](0010-hidden-reference-boundary.md).
  - The exclusion of `.flp` manipulation, and the withdrawal of FR-14 (its ID is not reused).
- **Not accepted by this ADR:** ADR-028 and ADR-030 to 033 stay Proposed. This ADR adopts the scope they were built for, not their designs.

## Decision

1. **Adopted scope (D15).** Bounded, operator-assisted runtime integration with FL Studio as an optional capability of the controlled coordinator, initially limited to:
   - **gain changes:** absolute `SetTrackGain` actions on owner-confirmed, calibrated mixer inserts, realized as fader writes through the scripting-API parameter-control adapter. Today's tested scope is cuts of 0.1–3 dB inside one calibrated range. Wider ranges, gain increases as actions, or further inserts need new calibration evidence and review. Raising a fader is allowed only as an authorized exact-target recovery (ADR-032);
   - **verified manual exports:** the operator saves and exports in FL Studio, and the system verifies the exported files and the DAW state before any candidate exists (ADR-033).
2. **Operator-assisted, never unattended.**
   - **Operator-owned steps:** the operator confirms the disposable project copy, the routing declaration and every export, and starts each continuation explicitly.
   - **Never automated:** saving, exporting, installing device scripts and recalibration.
   - **Run state:** a run waits in an explicit *awaiting operator export* state. It never fabricates a render result.
3. **Fader confirmation is not candidate readiness.**
   - A confirmed fader write establishes only that fader's reported readings.
   - Candidate audio exists only after a verified import with continuity before and after it (ADR-033). Only that audio enters analysis, verification and acceptance.
4. **Option A for DAW parameter state.**
   - **Representation:** `ProjectState` gains an explicit DAW parameter state for DAW-controlled tracks. It holds the binding reference and the selected and confirmed fader readings, and is separate from `TrackGainState`.
   - **`TrackGainState` keeps its ADR-019 meaning**: absolute gain on a fixed input stem. It is never written by the DAW path.
   - **Path separation:** offline states carry no DAW parameter state, and the offline renderer refuses a state that does.
   - Exact record names, field types and invariants belong to Increment 1 of the plan, with tests.
5. **Fader restoration is distinguished from complete project rollback.** Records and reports keep three claims apart:
   - **Fader restoration:** an exact, confirmed return of one fader's readings to a journaled target (ADR-032). It proves those readings only.
   - **Rollback of the selected state:** the saved accepted state stays selected, and two conditions hold:
     - its audio artifacts are retained unchanged and pass an integrity inspection;
     - both fader readings are shown **exactly** equal to the saved target by recorded evidence: a confirmed exact restoration, or a recorded reading equal to the target in both coordinates.

     This is what a DAW attempt's verified rollback may claim. For the retained audio, FR-10's "the previously accepted result exactly" holds.
   - **Resolution is not rollback** *(revised on 2026-11-19 by owner amendment)*. A resolution (ADR-032) may clear an uncertain command on evidence within tolerance. It never establishes a verified selected-state rollback, and neither does a reconciliation finding or a reading within tolerance.
   - **Complete project rollback:** the open project's routing, plugins, automation, saved file and freshly exported audio. This is **never claimed** by the DAW path. Records must state it as unverified rather than imply it.
6. **New requirements.** FR-19 and FR-20 are added to `requirements.md` with status Planned. Preparatory, unintegrated components exist (ADR-030 to 033).

## Rationale

- **The research question needs a real DAW case.** The owner's FL Studio exports are the first real musical material, and a bounded, attended loop lets the thesis evaluate the controller on a real DAW project without unattended automation, whose feasibility is unproven.
- **Every boundary rests on reviewed evidence.** The calibrated conversion (live run `live20261118codex`), recovery, the journal and export verification are each backed by tests and reviews.
- **A separate parameter state keeps both meanings honest.** Reusing `TrackGainState` would misdescribe a DAW project as fixed-stem processing.
- **Rollback must be claimed narrowly.** A fader restore cannot prove project restoration, so the stronger claim would be unfounded.

## Consequences

- **Planning.** The roadmap records the adopted optional scope. Coordinator integration follows the plan's increments 1 to 6, each tests-first and separately approved.
- **Requirements and offline path.** FR-10's offline semantics are unchanged. For DAW attempts, rollback is the selected-state rollback above, and its unverified project-level remainder is recorded in every rollback receipt and report.
- **Comparison conditions.** Results from DAW runs are labelled as such. Whether they enter the main evaluation, and under which comparison conditions (SR-01), remains an open question for the methodology, not a consequence of this ADR.
- **Unchanged exclusions:** `.flp` manipulation; plugin or GUI operation; unattended operation; automated save or export; EQ and dynamics actions in the DAW path.

## Sources

- Owner decision of 2026-11-18 adopting D15, choosing Option A, and requiring fader restoration to be distinguished from complete project rollback.
- [ADR-030](0030-daw-parameter-control-boundary.md) (the D15 proposal), [ADR-031](0031-fl-studio-control-journal.md), [ADR-032](0032-daw-exact-recovery-and-reconciliation.md) and [ADR-033](0033-daw-export-handoff-and-verified-import.md), all Proposed.
- [FL Studio action adapter design](../superpowers/specs/2026-11-18-fl-studio-action-adapter-design.md) and the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md).
