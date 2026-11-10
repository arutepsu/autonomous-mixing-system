# In-memory Run Journal Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans sequentially.

**Goal:** Implement RunJournal append/snapshot behavior alongside the existing acceptance journal for one controlled run.

**Architecture:** adapters.journal.run_memory composes InMemoryAcceptanceJournal. One outer lock serializes append, commit, query and snapshot. The append prefix is one immutable tuple of acknowledgements. Snapshot construction reuses RunJournalSnapshot validation. No core contract changes.

**Spec:** core.ports.run_journal.RunJournal and the approved controlled-coordinator design. First reports remain non_durable. The coordinator owns dispatch and run termination policy.

## API

InMemoryRunJournal(run_id: RunId, initial: AcceptedReference) validates through the existing acceptance journal, including initial sequence zero.

- append(event: RunTraceEvent) -> TraceAppendResult
- snapshot() -> PortResult[RunJournalSnapshot], returning Success on the base in-memory implementation
- commit(event: AcceptanceEvent) -> AcceptanceCommitResult
- query(request: AcceptanceQuery) -> AcceptanceQueryResult
- Read-only run_id, initial, accepted, commits and durability properties, matching the existing acceptance journal's meanings. durability is exactly "non_durable".

append validates the argument type before storage. Under the outer lock, reject a wrong run; look up existing event IDs before validating a new prefix; return the original acknowledgement for an equal event; reject a conflicting ID. For a new event, form the prospective event tuple and validate RunJournalSnapshot with the current acceptance history. Convert only that constructor's ValueError to TraceNotAppended. An invalid sequence, decreasing usage, invalid observation link or event after terminal therefore changes nothing. Unexpected errors propagate.

Build the acknowledgement and complete new prefix before publishing one tuple assignment. Derive trace events from the stored acknowledgements. Exact duplicates remain idempotent even after a terminal event or later acceptances.

commit/query delegate to the existing acceptance journal while holding the same outer lock. snapshot captures trace and acceptance prefixes under that lock and returns a consistent immutable snapshot. A failed append never removes or rewinds a prior committed acceptance. Acceptance sequence and trace sequence remain independent. This adapter enforces the supplied contracts, while the coordinator checks when a new acceptance is allowed and calls validate_acceptance_event before commit.

## Tests and scope

- Add adapters/journal/run_memory.py and tests/unit/test_memory_run_journal.py. Reuse public metadata factories from test_run_recording_contracts where helpful. No artificial musical demo.
- Extend test_adapters.py's exact journal edges/module inventory and runtime isolation check, preserving existing assertions.
- Update overview in place and append research-log evidence. No dependencies, installs, commits or pushes.

## Task

- [x] Record HEAD/status and baseline journal/record checks.
- [x] Write red tests for empty snapshot, append/idempotency/conflicts, exact sequence and usage, pending and duplicate observations, terminal boundary, wrong runs, original acknowledgement identity and immutable snapshots.
- [x] Test acceptance commits/query through the wrapper, historical duplicate commits, and that rejected/unknown trace-write wrappers preserve already committed edits. Use controlled test-only wrappers for lost acknowledgement before/after actual writes.
- [x] Test snapshot/commit serialization with synchronization events or barriers, without production fault controls or sleep-based sequencing. Propagate unexpected validation failures without mutating either prefix.
- [x] Implement composition and exact architecture updates. Run focused and architecture checks.
- [x] Update docs and run full suite, Ruff lint/format, CLI help and offline lock check with repository Python. Review final diff and leave uncommitted.
