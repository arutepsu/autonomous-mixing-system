# ADR-039 — Durable run and acceptance journals, restart and orphan detection (2026-11-20)

- **Status:** Proposed, for owner and Codex review. It records the Increment 5 plan of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md). *Implemented tests-first on 2026-11-20 with amendment A and the owner's clarification below; uncommitted for review. The first version was a proposal only.* It is the persistence ADR that G13 called for.
- **Builds on:**
  - ADR-027: the hexagonal layout;
  - ADR-032: the FL journal;
  - ADR-033 to 038, all Proposed.
- **Outside this ADR:**
  - CLI wiring and operator recovery (`daw recover`, Increment 6);
  - installation and live FL Studio calls;
  - schema migration;
  - network file systems;
  - any power-loss durability claim.

## Context

**The gap.** The run trace and the acceptance authority exist only in memory, so a suspended DAW run cannot outlive its process. The FL command journal is already durable, so after a restart the DAW side remembers writes that the run side has forgotten.

**What restart must achieve.** A suspended run must resume without repeating a write or an acceptance. Every other interrupted state must be recognized, and never repaired automatically.

## Decision (proposed)

1. **Storage.**
   - **The adapter:** one SQLite file per run, `SqliteRunJournal` in `adapters.journal`, implementing `RunJournal` and `RunExclusion`.
   - **Settings:** the FL journal's (`journal_mode=DELETE`, `synchronous=EXTRA`, `locking_mode=EXCLUSIVE`), verified at open.
   - **Tables:** append-only tables for the trace and the acceptances, each row with a SHA-256 fingerprint of its canonical record.
   - **Validation:** checked inside each write transaction and again on every read. A mismatch makes the snapshot fail; it never yields a partial trace.
2. **Exact serialization.**
   - **The codec:** an allowlisted canonical JSON codec over the public `core.records` dataclasses and enums.
   - **Encoding rules:** tagged records and enums; exact float `repr` with `-0.0` kept; timezone-aware datetimes only; sorted frozensets.
   - **Round-trip check:** every append and commit must satisfy `decode(encode(record)) == record`, or it is refused.
   - **Decoding:** through the record constructors, so every invariant is re-checked on load.
3. **Acceptance authority in the same file.**
   - **Semantics:** the existing commit and query semantics, each commit one transaction.
   - **Uncertain outcomes:** success is reported only after a confirmed `COMMIT`. Otherwise the result is `AcceptanceCommitUnknown` (or `TraceAppendUnknown`), resolved only by `query`. An unreadable store is `AcceptanceAuthorityUnknown`, never "absent".
   - **Cleanup after a failed transaction** *(amended 2026-11-20, owner review)*:
     - **Clean up:** `ROLLBACK` if a transaction is still open, close the connection, then reopen on a fresh connection with the lock and every fingerprint re-verified before any further operation.
     - **No leak:** no read is ever answered on a connection that had a failed transaction, so an uncommitted acceptance can never be reported as found.
     - **Every exception counts** *(revised after review on 2026-11-20; the first implementation cleaned up only after SQLite errors, record-validation errors and `ValueError`, so an unexpected exception before `COMMIT` left the transaction open, and a later query reported the uncommitted acceptance as found)*:
       - **Scope:** any exception after a write transaction is entered, for a trace append and an acceptance commit alike, triggers the rollback, close and verified reopen.
       - **Storage failures** are returned as unknown.
       - **Any other exception** propagates after the cleanup, as the port contract requires.
       - **Reads:** query and snapshot apply the same cleanup.
     - **If reopening fails:** the instance is unavailable:
       - `query` returns `AcceptanceAuthorityUnknown`;
       - `snapshot` returns a `PortFailure`;
       - `commit` returns `AcceptanceNotCommitted`;
       - `append` returns `TraceNotAppended`.
4. **Exclusive ownership across processes.**
   - **Holding:** the SQLite `EXCLUSIVE` lock is taken at open and held while the store is open. The operating system releases it when the process dies.
   - **Busy store:** a second opener gets `RunStoreBusy`, and resumption maps it to `CONTINUATION_IN_PROGRESS` with no effect.
   - **Not used:** no lease and no lock breaking.
5. **Restart.**
   - **Rebuilding the suspension:** `load_suspension` rebuilds `RunSuspended` from the durable snapshot. `resume_daw` and its Increment 3 admission are the only way to continue.
   - **Budgets and time:** budgets, the elapsed-time segments, tokens and charges are reconstructed from the checkpoint and the trace exactly as in-process. A new monotonic segment starts, and the pause is not charged.
   - **Charge agreement:** FL recovery charges must match the trace's recorded restoration intents.
6. **Interrupted states.**
   - **Classification:** `classify_run` assigns every non-terminal run one code: `SUSPENDED_RESUMABLE`, `CONTINUATION_INTERRUPTED`, `INTERRUPTED_BEFORE_WRITE`, `INTERRUPTED_WRITE_UNRESOLVED`, `INTERRUPTED_AFTER_WRITE`, `INTERRUPTED_BEFORE_SUSPENSION`, `INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT`, `INTERRUPTED_AFTER_ACCEPTANCE`, `INTERRUPTED_RECOVERY` or `INTERRUPTED_OFFLINE`.
   - **Precedence** *(amended 2026-11-20, owner review)*:
     - **Order:** the plan's amendment A2 fixes a first-match order, also covering crashes before the first write intent (`INTERRUPTED_BEFORE_FIRST_WRITE`) and after a continuation finished, before the next suspension (`INTERRUPTED_BETWEEN_ROUNDS`).
     - **Authority first:** an unrecorded committed acceptance (`INTERRUPTED_AFTER_ACCEPTANCE`) comes before every other rule.
     - **Specific effects next:** recovery, acceptance-commit, write and suspension effects come before the generic `CONTINUATION_INTERRUPTED`.
     - **The authoritative reference is always the committed acceptance head,** whatever the last trace event.
     - **Trace evidence of an acceptance** *(clarification, owner approval 2026-11-20)*: a recorded `AcceptanceCommitted`, **or a recorded, validated `AcceptanceFound` answer**. Without the second, a commit reconciled after a lost acknowledgement would leave a later suspension misclassified as `INTERRUPTED_AFTER_ACCEPTANCE`. A regression pins this (unknown commit, then found, then an intermediate acceptance, then a second suspension, which a restart resumes).
     - **Coverage:** an `UNCLASSIFIED` fallback exists only to fail closed, and a trace-prefix coverage test pins that it never occurs.
   - **Only `SUSPENDED_RESUMABLE` resumes.** Every other state is refused with its evidence and left to explicit operator recovery: nothing is restored, adopted, resolved, committed or re-dispatched.
   - **Authority after interruption:** a committed acceptance stays authoritative after any interruption.
7. **Orphaned FL commands.**
   - **At run start:** `daw_start_check` refuses before any decision when:
     - an FL command is unresolved;
     - the session's conversion is blocked;
     - the latest state-establishing FL event is not exactly the initial state's confirmed fader, or cannot be anchored.

     It uses the new read-only port method `latest_anchor()`; export anchors are unchanged.
   - **At resume:** FL commands or charges that the run's durable trace does not explain are refused (`ORPHANED_DAW_STATE`).
   - **No adoption, no automatic restoration.**
8. **Unchanged:**
   - `InMemoryRunJournal` stays the offline default;
   - the offline behaviour is byte-identical;
   - the termination-reason enum.
9. **Durability label.** `LOCAL_SQLITE_PROCESS_CRASH`. The evidence is subprocess crash tests only, and no power-loss claim is made. Network and synced storage are explicitly unsupported in this increment: documented, not detected. Tests use local temporary storage only.
10. **Durability provenance in results** *(amended 2026-11-20, owner review)*:
    - **The enum:** `StoreDurability` (`NON_DURABLE`, `LOCAL_SQLITE_PROCESS_CRASH`) becomes a trailing field of `RunJournalSnapshot`, defaulting to `NON_DURABLE`, and a read-only property of the `RunJournal` port.
    - **Reports and suspensions:** `RunReport.durability` and a new `RunSuspended.durability` return the trace's value, so reloaded suspensions carry it. In-memory results keep `"non_durable"`.
    - **The check:** the coordinator requires every snapshot to carry the journal's declared durability.
    - **Separate from acknowledgement:** store durability describes the store, and is independent of `unacknowledged_events` and `terminal_append`. Neither is derived from the other.

## Rationale

- **Recording before effect is what makes restart decidable.** The coordinator already records every intent before its effect, and the FL journal reserves before any byte is sent. After a crash, comparing the run's own durable trace with the FL journal therefore tells which effect may have happened, with no cross-run scan.
- **Resuming only from a recorded checkpoint keeps the guarantee simple.** Nothing before the checkpoint is re-executed, and the continuation's admission and continuity checks then run as they do in-process.
- **One file per run and the SQLite exclusive lock** reuse a mechanism the project already relies on for the FL journal, and avoid stale-lock heuristics.

## Consequences

- **The operator path grows.** Every interrupted state except a recorded suspension needs explicit operator recovery (Increment 6), even when nothing was written. This is accepted as fail-closed.
- **Benign drift blocks a start.** The start check refuses benign fader drift between runs, which also fails closed.
- **Decisions E1–E7 and open questions Q1–Q4** are listed in the plan for review.

## Sources

- [Coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md): G13, G14 and the Increment 5 plan.
- [ADR-032](0032-daw-exact-recovery-and-reconciliation.md) (FL journal) and [ADR-038](0038-coordinator-daw-recovery-routing.md) (recovery IDs and charges).

## Implementation notes (2026-11-20)

Recorded in the plan's "Increment 5 outcome"; each item is pinned by a test.

- **Port property name.** The port property is `store_durability: StoreDurability`. The adapters keep their existing `durability` string, so the in-memory label is untouched. *(The plan said the string would become the enum.)*
- **Fewer records than planned.**
  - **Classification:** `classify_run` and `reopen_daw_run` return `RunSuspended | RunInterrupted`. The planned `RunResumable`, `RunNotResumable` and `load_suspension` were folded into them, because the `RunSuspended` itself is the resumable result.
  - **Refusal codes:** the planned continuation refusal codes `RUN_INTERRUPTED` and `ORPHANED_DAW_STATE` were not added. The interruption codes carry that information, and an unexplained suspension is `SUSPENDED_DAW_STATE_UNEXPLAINED`.
- **Reads.** Every read re-checks every fingerprint. Decoding is cached by fingerprint, which is content-addressed, so a changed row is never served from the cache.
- **`latest_anchor`.** It skips refused writes, which wrote nothing, and anchors the latest other state-establishing event under the unchanged anchor rules. A source that cannot be anchored refuses the start (`DAW_CONTINUITY_FAILED`), failing closed.
- **Budget consequence.** After a found acceptance, the run has spent one recovery slot on the authority query. A further write therefore needs four configured slots (three reserved, ADR-038 D2).
- **Driver obligation (Increment 6).** FL inspection IDs are journaled, so every process must supply its own unique IDs. The tests give each process a distinct prefix.
- **Not separately probeable.** Skipping only the `ROLLBACK` while still closing and reopening is equivalent, because closing discards an open transaction. The probe that keeps the failed connection is caught.
- **Evidence.**
  - **Tests:** 16 codec and store tests, 16 in-process restart, classification and start-check tests, and 18 subprocess tests (crash points, ownership, the main restart path).
  - **Mutation probes:** 13 new, all caught. The Increment 4 set is still caught.
  - **What the evidence covers:** subprocess crashes only, with no power-loss claim, on local temporary storage.
