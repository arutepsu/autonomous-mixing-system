# ADR-033 — DAW pending-export handoff and verified audio import (2026-11-18)

- **Status:** Proposed, for owner and Codex review. It implements the owner-approved plan [2026-11-18-daw-export-handoff-import](../superpowers/plans/2026-11-18-daw-export-handoff-import.md) (revision 2 with the revision 3 continuity amendments) as unintegrated infrastructure.
  - It does not accept ADR-028, ADR-030, ADR-031 or ADR-032, and does not resolve scope decision D15. FR-14, ADR-001 and ADR-009 are unchanged.
  - It extends ADR-028's exact client-edge exception by one client, and ADR-032's recovery port by two methods; both changes are stated below.

## Decision

1. **A pending export is its own state, never a render result.**
   - After a confirmed or explicitly resolved DAW fader change, `DawExportHandoff.prepare` persists an immutable `DawExportRequest` in a fresh workspace and returns `DawExportAwaiting`: it awaits the operator.
   - `Renderer`, `RunRenderer`, `RenderedCandidate`, `RenderFailure`, `ExecutionReceipt` and `derive_candidate_checkpoint` are unchanged. No pending export is represented as a render success or a `CANDIDATE_READY` receipt.
   - Saving and exporting in FL Studio stay manual; no FL call is added.
2. **Candidate evidence comes only from the pure core assessment.**
   - `import_files` returns `DawFilesVerified`, which is file-level evidence only.
   - `core.admission.daw_export.assess_daw_import` combines it with two continuity snapshots and alone builds `DawImportVerified`. That record is not a receipt, a checkpoint or acceptance.
3. **Continuity is anchored in the FL command journal.**
   - **Anchor:** `DawRecoveryController.state_anchor(source_id)` returns a `DawStateAnchor`. Its source is the latest state-establishing ledger event: a confirmed apply or restoration, or a resolution. It carries the complete journaled `DawTrackBinding` and `DawControlProfile`, each rebuilt from the journal and required to serialize back to the journaled text. It also carries the reservation, resolution and ledger watermarks. While anything is unresolved, no anchor is returned.
   - **Currency is a record invariant:** an anchor whose source is not the latest event cannot be constructed.
   - **Preparation records journal-backed source evidence only.** No fresh inspection is captured there.
4. **Snapshots are coherent.**
   - `DawRecoveryController.continuity_snapshot(source_id)` holds the controller lock across the whole capture. It queries the anchor and control state, performs one fresh inspection, then queries both again.
   - The snapshot is returned only when the two queries agree. A ledger entry of any kind appended in between gives a retryable failure, never a mixed snapshot.
5. **Continuity rule, in records:** `daw_continuity_rejection`. Both the `before_import` and `after_import` snapshots require:
   - the anchor equal to the prepared one in every field, watermarks included;
   - nothing unresolved;
   - the conversion not blocked;
   - the bound identity on the tested build;
   - a fader reading exactly equal to the anchor's in both coordinates.
6. **Complete source context.**
   - `DawExportRequest` requires the full binding and profile to equal the anchor's, and one execution ID across the request, workspace and anchor.
   - A request that keeps the references but changes the project, track, baseline or any profile setting is refused.
7. **Exact coverage and explicit replacement.**
   - Each output replaces exactly one named before-checkpoint member of the same kind and source tracks.
   - The outputs are exactly the target stem, one stem per declared dependent, and one master mix. Unknown routing raises `RoutingUnknownError`.
   - Members that are not replaced are retained.
8. **The timeline is declared; physical alignment is unverified.**
   - Rate, channels and frames are verified against the declared metadata.
   - The timeline origin is provenance only and is recorded as `DawAlignmentStatus.DECLARED_TIMELINE_PHYSICAL_UNVERIFIED`.
9. **Workspace identity and file rules** (`adapters.rendering.export_handoff`).
   - **Identity:** preparation records the directory's and request file's `(st_dev, st_ino)` (the volume serial and file index on NTFS), plus the request file's size and SHA-256. The import re-checks all of them, so a replaced directory is refused even when the original request file is copied or moved into it.
   - **Names:**
     - one path component;
     - not the reserved `export-request.json`;
     - no Windows-invalid characters;
     - no trailing dot or space;
     - no device names;
     - `.wav` only;
     - distinct after NFC normalization and case folding.
   - **Listing:** exactly the request file plus the declared outputs.
   - **Every file object** must be:
     - regular;
     - not a link or reparse point;
     - of one hard link;
     - of a distinct physical identity;
     - directly inside the workspace.
   - **Request file read:** checked through the open handle and bounded to the recorded size plus one byte.
   - **WAV content** goes through the shared verifier `check_resolved_wav_integrity`, and the container and subtype must equal the requested format.
   - **Change detection** *(revised after review on 2026-11-18; the first version compared identity and link count only, so an in-place write after verification passed)*:
     - after each file's read, its identity, size, modification and change timestamps, link count, mode and file attributes must equal what was observed before the read;
     - before returning, the request file and every output are re-checked against those observations, so an earlier file that changed during a later file's read is caught;
     - any difference is `WORKSPACE_CHANGED`.
   - **Writes:** nothing is converted or written.
10. **ADR-028 client edge.** `adapters.rendering.export_handoff` is one more listed client of `adapters.audio_io`, with the exact edges `locations`, `integrity` and `limits` and no private names. The rendering adapter hosts it because workspace allocation and revalidation are rendering internals that rule 3 forbids other adapters to import.

## Rationale

- **No fabricated success.** A DAW change is audible only after a manual export, so the result must be a waiting state, not a fabricated render success.
- **Continuity needs an ordered, persistent source.** The FL journal is the only one, so the anchor and its watermarks come from it.
- **Coherent capture.** Without the coherent snapshot, a command could slip between the separate anchor, inspection and control-state calls.
- **References are not identity.** A reference can be reused with other contents, so complete records are compared.
- **Identity detects replacement.** Bytes alone cannot reveal a replaced directory or file; file-system identity can.

## Consequences

- **What it establishes.** Continuity establishes observed agreement at preparation, before the import and after it. It does not establish that nothing moved during the export: a manual move and move-back, or a routing or plugin change, is undetectable.
  - A hash establishes file identity only. Names, the attestation and the metadata title authenticate nothing.
  - Unchanged faders do not imply unchanged audio, and the master is not assumed to equal the stems.
- **Fail-closed behaviour.**
  - Any later reservation supersedes an anchor, including a write the device refused. The system then cannot export until a new confirmed write or resolution exists. This is conservative by design; see the open question in the plan.
  - Any ledger entry, including a local-refusal marker, breaks continuity with an earlier preparation.
- **Not race-free.** None of the file checks is race-free, and identities can be reused after deletion. Timestamp checks depend on the file system's timestamp granularity and on writers not resetting timestamps. A change after the final re-check is not detected.
- **Analysis profile.** Stereo exports stay stereo. Any conversion for analysis is a later, versioned derived artifact; dist10's mono 44.1 kHz int16 profile is not widened.
- **The later coordinator** must:
  - anchor, build and prepare the request, record the awaiting state, then stop;
  - in the explicit continuation, take `before_import`, call `import_files`, take `after_import` and call `assess_daw_import`;
  - only then build the receipt and candidate checkpoint, analyse under a declared profile, verify, and accept or recover.
- **Evidence:** tests with real temporary directories, tiny synthetic WAVs and the real FL journal and controller against device v2 on a fake host. That is not live FL evidence.

## Sources

- [Export handoff plan](../superpowers/plans/2026-11-18-daw-export-handoff-import.md), revision 2 with the revision 3 amendments.
- [ADR-028](0028-shared-read-only-audio-access.md), [ADR-030](0030-daw-parameter-control-boundary.md), [ADR-031](0031-fl-studio-control-journal.md) and [ADR-032](0032-daw-exact-recovery-and-reconciliation.md), all Proposed.
- [Approved adapter design](../superpowers/specs/2026-11-18-fl-studio-action-adapter-design.md), "Increment 2".
