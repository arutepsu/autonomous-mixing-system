# ADR-031 — FL Studio runtime dispatch journal and local replay (2026-11-18)

- **Status:** Proposed. Implements the owner-approved transport-adapter plan as unintegrated feasibility infrastructure. Does not accept ADR-030 or resolve scope decision D15. FR-14, ADR-001 and ADR-009 are unchanged.

## Decision

The `adapters.fl_studio` component implements the parameter-control port separately from audio rendering. A local SQLite command journal is the dispatch-intent and replay authority for one configured DAW session. It is not the acceptance journal, RunJournal, project checkpoint or proof of restored audio.

The configured session key must identify the MIDI endpoint pair and calibrated profile. One local database is authoritative for that session; creating a fresh database for another attempt defeats the guard and is unsupported. A caller owns the journal lifetime. Exclusive SQLite locking excludes another connection while a controller session owns it; an RLock serializes each controller operation. SQLite also checks each reserve transaction against all persisted pending/uncertain writes and conversion blocks.

Pending intent must commit before the first send. Configuration is checked by read-back: `journal_mode=DELETE`, `synchronous=EXTRA`, `locking_mode=EXCLUSIVE`, zero busy timeout. Serialized requests include the complete action, execution ID, revision, policy reference, binding, observed/confirmed state and profile; the wire command and content fingerprint are stored as well. Schema version 1 and a session-key mismatch fail closed. Inspection IDs are persistently reserved in a separate table so a delayed read reply or device duplicate history cannot be confused with a new apply command.

SQLite synchronization depends on the local OS, filesystem, VFS and hardware honoring its requests. The settings follow [SQLite's synchronization reference](https://www.sqlite.org/pragma.html#pragma_synchronous) and [exclusive-locking reference](https://www.sqlite.org/pragma.html#pragma_locking_mode). Process-exit tests establish process-crash persistence only; no physical power-loss test or hardware durability guarantee is claimed.

## Replay, uncertainty and storage failure

- Same ID and identical complete request: replay the committed outcome locally without opening MIDI. Confirmed/off-target outcomes set `replayed=True`; refused/uncertain outcomes remain unchanged.
- Same ID with different content, or an apply ID already used for inspection: refuse, without overwriting prior evidence.
- Pending intent without an outcome: report unresolved and never resend automatically, including after device-script reload.
- Every unknown after intent/dispatch is conservative uncertainty. Generic unrecognized wire errors do not become pre-write refusals.
- Outcome commit must succeed before returning confirmation. A failed or ambiguous commit poisons the current controller. If a later reopen finds an intact committed validated outcome, it can replay that evidence; otherwise the operation remains unresolved. Never infer that a failed commit acknowledgement proves the transaction rolled back.
- A storage-failure fallback is retained in memory for the current controller only. It is not a durable receipt; after restart, persisted journal state is authoritative. A pre-intent storage failure sends nothing. No new write is permitted on the poisoned instance.
- Known native cleanup failure retains the native transport/buffers and poisons further opens, including failure during partial construction. Successful reply evidence already committed is preserved if cleanup subsequently fails.

Inspect is observational: it does not clear unknown-write or conversion blocks. This deliberately supersedes the research CLI's auto-clear-on-inspect behavior for this runtime component, in accordance with the approved adapter design. Exact-target recovery and explicit reconciliation require a later contract and authorization. The earlier CLI remains frozen for reproducing its trials.

## Ownership and compatibility

`scripts/fl_studio/bridge_protocol.py`, `bridge_client.py` and `device_bridge.py` are unchanged historical experiment implementations, pinned by hashes in the implementation plan. Runtime `protocol.py` owns an initially byte-identical snapshot; no runtime module imports research scripts. Tests verify the frozen source and wire/conversion parity. There is no claim that current repository bytes equal all earlier live-install hashes.

The configured profile is restricted to the tested FL build/API, project title, insert, conversion and calibrated range. dB endpoints are derived at full precision. The actual normalized target is recomputed before dispatch. A matching wire reply is still checked against exact core invariants; a rounding-edge disagreement becomes uncertain, never a broadened success tolerance. The normalized target remains an adapter claim; core does not evaluate the curve.

The native transport is lazily loaded and owns every buffer until native completion/reset and unprepare. Failure to release ownership retains the object rather than freeing active memory. Fake-driver tests establish code behavior, not real driver validation.

## Consequences and remaining work

- No coordinator, runnable driver or CLI uses this adapter. No installation, live command, project save or export occurred in this implementation task.
- Runtime adoption still needs D15/new requirement and ADR-030 approval. SQLite journal adoption remains Proposed here.
- Recovery, reconciliation, pending-export continuation, verified audio import and coordinator integration remain missing. A confirmed fader result cannot make a candidate ready or accepted.
- Live tests must be separately reviewed/authorized and repeated against this runtime implementation. Existing bridge trials are historical evidence, not a live validation of the extracted transport and new journal.

## Sources

- [Owner-approved implementation plan](../superpowers/plans/2026-11-18-fl-studio-transport-adapter.md).
- [DAW parameter-control boundary](0030-daw-parameter-control-boundary.md), Proposed.
- [Approved adapter design](../superpowers/specs/2026-11-18-fl-studio-action-adapter-design.md).
