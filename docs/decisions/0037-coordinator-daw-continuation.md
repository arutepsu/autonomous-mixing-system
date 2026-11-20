# ADR-037 — Coordinator DAW continuation: import, verification and acceptance (2026-11-19)

- **Status:** Proposed, for owner and Codex review. It records Increment 3 of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md), with amendment A and the approval's two constraints. It was implemented tests-first and is uncommitted.
- **Builds on:** ADR-033 to 036, all Proposed.
- **Outside this ADR:** durable restart (Increment 5), recovery routing (Increment 4), CLI wiring (Increment 6), installation, live FL Studio calls, and the offline commit-exception issue (a separate task).

## Decision

1. **Entry point and admission.** `resume_daw(suspended, manifest, ...)` returns `RunReport | RunSuspended | DawContinuationRefused`.
   - **Lock:** it takes the run's exclusive ownership (`RunExclusion`) before reading the trace, and holds it through admission, execution, any next suspension and finalization.
   - **Admission** (`admit_continuation`) returns, without any effect:
     - `ALREADY_CONTINUED`, citing the completion evidence: a `DawContinuationFinished`, a terminal snapshot, or a later checkpoint;
     - `MANIFEST_CONFLICT`;
     - `CONTINUATION_INTERRUPTED`, when a start exists without any completion evidence;
     - `CONTINUATION_IN_PROGRESS`, when the lock is held;
     - `NOT_SUSPENDED`;
     - `REQUEST_MISMATCH`, when the supplied `RunRequest` differs from the run's recorded one, the trace's first event, in any field (budgets, criteria, DAW session and the rest). This is decided before anything is appended or dispatched *(revised after review on 2026-11-19; the first version compared only the run ID and initial state, so a changed budget was caught only at the final report)*.
2. **One shared lock authority per run.** The in-memory adapter keeps one non-reentrant lock per run ID in a module-level registry, shared by every journal instance of that run, so a caller cannot bypass exclusion with another instance. It is NON_DURABLE; Increment 5 supplies a cross-process authority.
3. **The continuation, in order:** `DawContinuationStarted`; the before-import coherent continuity snapshot (one retry of a retryable failure); the verified import; the after-import snapshot; `assess_daw_import` (recorded); the DAW receipt and candidate; one candidate analysis with exports bound to the export root; the `ATTEMPT` verification; DAW acceptance.
   - **Iteration:** an accepted intermediate candidate is followed by `DawContinuationFinished(ACCEPTED)` and further decision rounds, which may suspend again.
4. **Native channels, one analysis profile.**
   - **Preflight:** it now requires every replaced artifact to be a native export with the session's render spec, rate, timeline start and frame count. Anything else is refused with `INCOMPATIBLE_AUDIO` before any write. Outputs keep their native channel count, and nothing is converted.
   - **Analysis:** the supported profile is `FileAudioAnalyzer`, full-band peak and RMS, mono or stereo, with sources matched by exact metadata.
   - **Masking:** the mono dist10 masking profile (ADR-029) is unchanged and unsupported for DAW candidates.
5. **Acceptance authority (amendment A1, with the approval's constraint).** The dedicated `accept_daw` and `reconcile_daw_acceptance` handle DAW candidates; the offline acceptance, reconciliation, recovery and promotion code is unchanged and unused here.
   - **Before the commit:** the authority is marked unknown, with `ACCEPTANCE_COMMIT_UNCERTAIN` pending.
   - **Committed or validated found:** the new reference is selected (`accepted`, `authority_known`) before any promotion, recording or other fallible work. For a found answer, this includes recording the answer's own observation: the answer is validated against the submitted event and established first. A confirmed-absent answer likewise sets its recovery-required marker before it is recorded *(revised after review on 2026-11-19; the first version recorded the answer first, so an exception there reported unknown authority although the journal held the acceptance)*. `verified` stays `None` until promotion completes, or until the found state passes its integrity inspection. Later failures keep the accepted authority.
   - **Confirmed absent:** never a restoration.
   - **Unknown, or not queried:** `ACCEPTANCE_COMMIT_UNCERTAIN`.
   - **Event validation:** `validate_daw_acceptance_event` re-derives the receipt, candidate and verdict with the DAW derivation. The offline validator refuses DAW states.
6. **DAW promotion (`promote_daw`):** every exported stem's binding, the exported master as the selected mix, and every candidate artifact bound to the session's export root.
7. **No rollback claim while recovery is absent.** An unaccepted DAW candidate ends the run with `REQUIRED_RECOVERY_NOT_PERFORMED`. This covers rejection, inconclusive verification, analysis failure, refused or absent acceptance, and pre-commit cancellation or time.
   - **The attempt** is `UNRESOLVED`, with a DAW-scope `RollbackReceipt` whose status and `retained_audio` are both `UNCERTAIN`, with no fader evidence. Its explanation says "recovery not attempted": the fader is not restored and the selected state is not re-established (approved by the owner).
   - **Other reasons:** a continuity rejection is `DAW_CONTINUITY_FAILED`, and a file rejection is `EXPORT_IMPORT_REJECTED`.
   - **In every case,** nothing is restored or cleared, and the final state is `UNKNOWN`.
8. **Pause accounting.** `RuntimeProgress.elapsed_before` and `segment_start` count controller time per clock segment (`runtime.elapsed_at`, also used by `finalize`). A continuation starts a new segment, so the operator pause is never charged. Its UTC length is recorded in `DawContinuationStarted`.
9. **Record and adapter changes:**
   - **`DawCorrectionSuspended`** gains `stems`, `mix_id` and `roots`, validated against the before state.
   - **New records:** `DawContinuationStarted`, `DawContinuationFinished` (with `DawContinuationDisposition`) and `DawContinuationRefused` (with `DawContinuationRefusalCode`).
   - **New trace payloads:** `DawImportVerified` and `DawImportRejected`.
   - **`FileExportHandoff`** accepts a driver-supplied state `inventory`, from a before checkpoint to its state and protected locations, so a run can prepare a second export against its accepted state. It still requires the state's audio to equal the request's checkpoint.

## Rationale

- **Unaccepted candidates.** Without recovery, the only truthful outcome for an unaccepted DAW candidate is to stop and say that recovery was not attempted.
- **Acceptance authority.** Separating acceptance uncertainty from recovery failure, and selecting an established reference first, keeps the run report from naming the wrong authoritative state after a commit exception or a failed promotion.
- **Completion evidence and the lock.** Explicit completion evidence, held under one per-run lock, makes duplicates decidable even when a continuation reaches another suspension.

## Consequences

- **Deviations from the plan, both recorded in the plan:**
  - **C6:** the offline `verify_candidate` is not split. The DAW candidate tail lives in `core.loop.daw_continuation`, reusing the analysis and verification building blocks, so the offline path stays byte-identical.
  - **C8:** realized as the dedicated DAW acceptance path of amendment A1.
- **The handoff inventory is driver wiring.** In tests it reads the run's accepted states from the journal; Increment 6 provides it from the run journal.
- **Evidence.**
  - **Tests:** 32 continuation tests (27 before the review fixes) in `tests/unit/test_daw_continuation.py`. They use the real FL runtime against device script v2 on a fake host, the real export handoff, the real `FileAudioAnalyzer` and the real `FileStateIntegrityInspector` over temporary synthetic stereo WAVs, with labelled doubles for decisions and diagnosis.
  - **Handoff:** two new inventory tests.
  - **Mutation probes:** 16 new (14, plus 2 for the review fixes), each reverted byte-identically and caught. Every earlier probe set is still caught.
  - **None of this is live FL evidence.**

## Sources

- [Coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md): Increment 3 plan and amendment A.
- Owner approval of 2026-11-19: accepted authority selected before promotion, one shared lock authority per run, and the `UNCERTAIN` rollback receipt with explicit "recovery not attempted" wording.
