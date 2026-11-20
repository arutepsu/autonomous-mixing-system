# ADR-036 — Coordinator DAW write path up to the export suspension (2026-11-19)

- **Status:** Proposed, for owner and Codex review. It records Increment 2 of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md), implemented tests-first and uncommitted. It builds on [ADR-034](0034-bounded-operator-assisted-fl-studio-integration.md) and [ADR-035](0035-coordinator-daw-execution-path.md), both Proposed.
- **Outside this ADR:** continuation (Increment 3), full recovery routing (Increment 4), durable run journals (Increment 5), CLI wiring (Increment 6), installation and live FL Studio calls.

## Decision

1. **Entry points.**
   - `core.loop.coordinator.run_daw(...)` returns `RunReport | RunSuspended`. It takes the DAW parameter controller, the recovery controller and the export handoff in place of the offline renderer.
   - `run(...)` keeps its signature and offline behaviour, and refuses a request with a DAW session. `run_daw` refuses one without.
   - Both share the observation, decision and admission stages; `ordinary` takes the execution step as a `partial`.
2. **Capability view and admission.**
   - In a DAW session, the view's execution bounds are `daw_gain_bounds` of the selected state's confirmed fader (Option A state), intersected with the session's policy permission. `admit_action` and the advertised-bounds check are unchanged.
   - **DAW preflight:** `core.admission.daw_correction.preflight_daw_correction` replaces the render preflight. It reruns admission, checks the export workspace and the new identities, and builds the `DawCorrectionPlan` (the exact affected set, ADR-035 G5). It is recorded as `DawPlanReady`.
   - **Command ID:** `apply-<execution token>`, used by every retry and replay.
   - **Output files:** named from output order only. Each output is declared with the replaced artifact's channel count.
3. **Before any write** (`core.loop.daw_execution.execute_daw`):
   - the FL journal's control state, recorded;
   - one fresh inspection (`DAW_INSPECT`);
   - `gate_daw_apply` with the journal's blocked conversions and its first unresolved command. Its verdict is recorded.
   - **If nothing is to be written:**
     - **Rejection:** `REJECTED` at the preflight stage; the loop continues.
     - **`DawNoWrite`:** `NOT_EXECUTED`; the loop continues.
     - **Unavailable control state or inspection:** `NOT_EXECUTED`, and the run ends with `EXECUTION_PREPARATION_FAILED`.
4. **One write**, after the intent is recorded and charged as `DAW_WRITE`; the FL journal commits its own intent before any byte is sent. Outcomes:
   - **`DawWriteRefused`, a pre-write refusal:** `FAILED` with `NOT_STARTED`. The verified reference is re-established (nothing was written) and the loop continues.
   - **`DawWriteConfirmed`:** continue to point 5.
   - **`DawWriteOffTarget`, `DawWriteUncertain` or an invalid response:** see point 6.
5. **After a confirmed write:**
   1. the journal-backed anchor (`DAW_STATE`). It must be this write's `CONFIRMED_WRITE` anchor, carrying exactly this request and reading exactly the confirmed `after`;
   2. the `DawExportRequest`;
   3. `prepare` (`EXPORT_PREPARE`);
   4. the complete `DawCorrectionSuspended` checkpoint as the trace's last event;
   5. `RunSuspended`.

   **No `ExecutionReceipt`, analysis, verification or acceptance follows a confirmed fader write.**
6. **Interim termination until Increment 4 (stated before implementation).** Whenever the fader is, or may be, away from the selected state, the run ends at once:
   - **Off-target:** `DAW_CONVERSION_BLOCKED`.
   - **Uncertain or invalid:** `DAW_COMMAND_UNRESOLVED`.
   - **A confirmed write followed by any failure:** `REQUIRED_RECOVERY_NOT_PERFORMED`. This covers the anchor, the export request, preparation, the checkpoint, recording, cancellation or time.
   - **In all these cases:**
     - **No recovery:** no restoration, reconciliation, resolution or `RollbackReceipt`.
     - **No clearing:** nothing is cleared in the FL journal, so its unresolved command or conversion block keeps blocking every later write.
     - **Final state:** the verified reference stays cleared, so the final state is `UNKNOWN`; no rollback is claimed.
     - **Feedback:** `UNRESOLVED`, `UNCERTAIN`, with no rollback status.
   - **Preserved reasons:** `DAW_COMMAND_UNRESOLVED` and `DAW_CONVERSION_BLOCKED` join the reasons that `finalize` and `RunReport` keep when the terminal event is not acknowledged.
   - **Unexpected exceptions are phase-aware** *(revised after review on 2026-11-19; the first version recorded every unexpected exception as `RUNTIME_CONTRACT_FAILED`, so an `OSError` in export preparation lost `REQUIRED_RECOVERY_NOT_PERFORMED`)*. `RuntimeProgress.pending_effect`, a trailing field that defaults to `None`, names the reason an unexpected exception must keep:
     - `DAW_COMMAND_UNRESOLVED` from just before the apply call, because the write may have been dispatched;
     - `DAW_CONVERSION_BLOCKED` once an off-target write is known;
     - `REQUIRED_RECOVERY_NOT_PERFORMED` once a write is confirmed;
     - cleared after a pre-write refusal.

     The marker is set from the validated reply *before* its observation is recorded, so an exception while recording the observation keeps the right reason *(second revision after review on 2026-11-19; the first set it after recording, so a confirmed write whose observation failed to append was reported as `DAW_COMMAND_UNRESOLVED`)*. A refusal also re-establishes the verified reference at that point.

     The coordinator's best-effort terminal recording uses it, names the exception type and message, and re-raises the original exception. It restores nothing and clears no journal state. With nothing pending, including the offline path, an unexpected exception is still `RUNTIME_CONTRACT_FAILED`, with the explanation unchanged.
7. **New trace payloads:** `DawPlanReady`, `DawControlState`, `DawApplyPermitted`, `DawNoWrite` and `DawApplyRejected`. The `RunReport` usage check also covers `daw_writes`.
8. **Feedback stage naming.** The DAW write reuses `FeedbackStage.RENDER` as the execution stage, and the gate reuses `PREFLIGHT`. No feedback enum changes.

## Rationale

- **Shared stages.** Reusing every observation, decision and admission stage keeps the two paths comparable. Only execution differs.
- **Stopping without recovery.** Ending immediately is the only safe behaviour while recovery routing is absent. It leaves the FL journal's own blocks authoritative and claims nothing.

## Consequences

- **Coverage.** A DAW run reaches the operator pause, or ends safely. Resuming a suspended run is not possible until Increment 3.
- **Requirements.** FR-19 and FR-20 stay Planned. Their write and suspension halves are implemented, unintegrated with continuation.
- **Evidence.**
  - **Tests:** 22 coordinator-level tests (15 before the review fixes) in `tests/unit/test_daw_coordinator.py`. They use the real FL protocol, exchange, journal and controller against device script v2 on a fake host, the real export handoff on temporary directories, and labelled doubles for analysis, diagnosis, decisions and integrity. The `DawNoWrite` branch uses a labelled double of the gate, because the calibrated capability view never advertises a zero cut.
  - **Mutation probes:** 16 (11, plus 5 for the review fixes), each reverted byte-identically and caught. The 33 Increment 1 probes are still caught.
  - **None of this is live FL evidence.**

## Sources

- [Coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md), "Increment 2 design note"; ADR-030 to 035.
- Owner instruction of 2026-11-19 approving Increment 2 and requiring the interim termination of off-target, uncertain and post-write failures to be stated before coding.
