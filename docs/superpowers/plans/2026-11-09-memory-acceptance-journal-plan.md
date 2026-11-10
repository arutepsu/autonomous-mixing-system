# In-memory Acceptance Journal Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Work sequentially in the current session.

**Goal:** Implement the AcceptanceJournal contract for the first NON_DURABLE integration milestone.

**Architecture:** One adapter in adapters.journal.memory owns one run's ordered acceptance commits. Its selected reference is derived from its committed prefix. It imports records and standard-library helpers.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Existing core.ports.acceptance.AcceptanceJournal contract and control-loop attempt contract section 29.

## Public API

`InMemoryAcceptanceJournal(run_id: RunId, initial: AcceptedReference)` requires the existing types and initial.sequence == 0. It provides commit and query with the exact Protocol signatures.

Read-only properties:
- run_id: RunId
- initial: AcceptedReference
- accepted: AcceptedReference, initial for an empty journal, otherwise the last acknowledgement's reference
- commits: tuple[AcceptanceCommitted, ...], in commit order
- durability: str, exactly "non_durable"

The adapter remains mutable internally while returning immutable records and tuples. A fresh instance starts empty even if another instance used the same run ID.

## Commit

Check event type before touching storage. Serialize commit and query using one adapter-owned threading.Lock so queries observe a consistent prefix.

1. A wrong run returns AcceptanceNotCommitted with an explanation.
2. If the acceptance ID already exists, an equal event returns the original acknowledgement. A different event under that ID returns AcceptanceNotCommitted. Perform this lookup before checking the expected head, so exact duplicates still succeed after later commits.
3. Otherwise require event.expected == current accepted reference, comparing complete state and sequence. A mismatch returns AcceptanceNotCommitted.
4. Build AcceptanceCommitted with the candidate and expected.sequence + 1. Append exactly once and publish the new committed prefix as one internal update.

Use a tuple of acknowledgements as the authoritative prefix and derive accepted from it. This avoids separately advancing a head that could diverge if an append fails. The bounded first loop makes a linear ID lookup sufficient. Unexpected errors propagate.

The coordinator must call validate_acceptance_event before submission and establish run/attempt/decision membership and file integrity. The adapter respects the structural event contract and expected-reference checks. It cannot import loop or verification stages.

## Query

Check request type. A query for this run returns AcceptanceFound with the stored event and its historical accepted reference, or AcceptanceAbsent when the ID has never committed. An ID rejected earlier can therefore be absent.

A wrong-run query returns AcceptanceAuthorityUnknown with the requested run/ID and an explanation that this instance serves a different run. It cannot establish authority for that run.

The base implementation has definite in-memory outcomes. To test a lost acknowledgement, a test-only wrapper calls commit and then returns AcceptanceCommitUnknown. Querying the same journal must reveal the stored event. Another test-only wrapper may return unknown without writing; the query then confirms absence. Do not add production fault flags, retries or sleeps.

## Owned paths

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve other work. Use labelled metadata fixtures. No installs, dependencies, commits or pushes.

- Add src/autonomous_mixing/adapters/journal/__init__.py with only a docstring and src/autonomous_mixing/adapters/journal/memory.py.
- Add tests/unit/test_memory_acceptance_journal.py. Keep helpers local rather than importing another test module's private fixtures.
- tests/architecture/test_adapters.py may gain focused runtime-isolation/import tests for this adapter. Keep rule (c), exact rendering edges and every existing assertion. Existing whole-tree scans should discover the new package automatically.
- Update overview in place and append research-log evidence and limits. Preserve requirement statuses.

## Task 1

- [ ] Record baseline HEAD/status and inspect acceptance contracts. Run baseline checks.
- [ ] Write red tests for initial state/sequence validation, first commit, sequential commits, historical exact duplicates, same ID/different event, stale full references (including same revision with altered state), wrong-run commits and queries, definite absence, original object retention and read-only properties.
- [ ] Test two competing events with the same expected head, using a thread barrier to start both commit calls. Require exactly one commit and one rejection. Assert the prefix and selected reference agree. Avoid sleep-based tests.
- [ ] Test the two lost-acknowledgement wrappers and validate all returned responses using existing port validators. Confirm fresh-instance history loss and the non_durable property.
- [ ] Implement the adapter. Run focused tests, acceptance contracts and architecture tests.
- [ ] Update docs and verify full suite, Ruff lint/format, CLI help and uv lock --check. Use uv run --locked where available. Report omitted checks.
- [ ] Review file scope and report exact interfaces, red/green evidence, results, deviations and git status. Leave uncommitted for Codex review.
