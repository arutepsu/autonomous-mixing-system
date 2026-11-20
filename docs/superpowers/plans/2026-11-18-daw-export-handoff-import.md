# DAW Pending-Export Handoff and Verified Audio Import — Implementation Plan

> **Status: revision 2 approved on 2026-11-18 with the continuity amendments below (revision 3), executed tests-first in the same session.** After approval, execute task by task with tests first, and leave everything uncommitted for owner and Codex review. No coordinator integration, automated saving or exporting, installation, live FL calls, commits or pushes.

**Revision 2** answers the review of revision 1:

| Review point | Change | Section |
| --- | --- | --- |
| State continuity | A journal-backed `DawStateAnchor` with ledger watermarks; continuity checked before and after the file verification by a pure core rule; observed agreement only, never "nothing moved" | State anchor and continuity |
| Alignment | The timeline is **declared, physical alignment unverified**, recorded as such; the tautological shifted-start rejection is removed | Alignment |
| Workspace identity | Preparation records the directory's and request file's file-system identity; continuation verifies both; tested with a replacement directory holding a copied request | Workspace identity |
| Replacement | An explicit output→superseded mapping with complete coverage, master included; the configured `ProjectState` must match the before checkpoint; the workspace execution ID must match | Records |
| Filenames and file objects | Reserved name, native collisions, links and reparse points, hard links, duplicate physical files, and bounded safe reads for the request file too | File rules |

**Revision 3 (owner-approved amendments, 2026-11-18).** These supersede the conflicting text below:

1. **Coherent snapshots.** The three separate calls (anchor, inspect, control state) could straddle another command. The recovery port gains `continuity_snapshot(source_id)`. It holds the controller lock across the capture, queries the anchor and control state before and after the inspection, and returns the snapshot only when both queries agree. Otherwise it returns a retryable `PortFailure`. The test inserts a ledger entry (a reservation, and separately a local-refusal marker) between the first anchor query and the inspection.
2. **Complete source context.** `DawStateAnchor` carries the journaled `DawTrackBinding` and `DawControlProfile` records in full, rebuilt from the journal and checked to serialize back to the journaled text, instead of their references. `DawExportRequest` carries the full profile and requires `binding == anchor.binding` and `profile == anchor.profile`. A request that keeps the references but changes the project, track, baseline or any profile setting is refused.
3. **Records-layer validation.** The anchor currency rule, the continuity rule, the file-name rule and the candidate-artifact derivation live in `core/records/daw_export.py`. No record constructor imports `core.admission`. `core/admission/daw_export.py` holds only `assess_daw_import`, and `check_state_anchor` is dropped: an anchor that is not the latest state-establishing ledger event cannot be constructed. The anchor gains `resolution_watermark` (the latest `RESOLVED` seq) beside the reservation and ledger watermarks.
4. **Preparation records journal-backed source evidence only.** No fresh inspection is captured at preparation, and the awaiting record says so. The first fresh reading is the `before_import` snapshot.

Smaller adjustments made during implementation: `DawExportRequest.project_id` is derived from the before checkpoint rather than stored. `DawImportVerified` holds the `DawFilesVerified` result (request, manifest and evidence) rather than copies. A file identity that changes after the content read gives `WORKSPACE_CHANGED`. `DIMENSIONS` covers declared rate, channel and frame mismatches: every output declares the request's timeline, so equal rates and frame counts across outputs follow.

**Goal:**
- After a confirmed or explicitly resolved DAW fader change, create an immutable export request and record it as *awaiting operator export*. It is never candidate-ready and never accepted.
- In a separate, explicit continuation, verify the operator's manifest and files with the existing shared audio-integrity path, and confirm observed state continuity before and after.
- Only then produce candidate evidence for the verifier and controller to judge.

**Builds on:**
- [adapter design](../specs/2026-11-18-fl-studio-action-adapter-design.md), "Increment 2";
- [DAW control contract](2026-11-18-daw-control-contract.md), [transport adapter](2026-11-18-fl-studio-transport-adapter.md) and [recovery and reconciliation](2026-11-18-daw-recovery-reconciliation.md), all committed in `335e904`;
- [ADR-028](../../decisions/0028-shared-read-only-audio-access.md) and ADR-030 to 032 (all Proposed).

**Baseline:** HEAD `335e904`, clean tree; recovery review passed, with 746 focused tests independently verified.

## Global constraints

- **Preserved unchanged:**
  - `Renderer`, `RunRenderer`, `RenderPlan`, `RenderedCandidate` and `RenderFailure`;
  - `ExecutionReceipt`/`ExecutionStatus` and `derive_candidate_checkpoint`;
  - `TrackGainState`, `SetTrackGain` and the offline gain/mix path;
  - `DawParameterController` and the existing `DawRecoveryController` methods. This slice **adds one method**, `state_anchor`.

  No pending export is ever represented as a render success, a `RenderedCandidate` or a `CANDIDATE_READY` receipt.
- **Originals:** original projects, baseline exports and all before-state audio are never written, moved or renamed. Exports land only in a fresh, allocated workspace that overlaps no protected location.
- **No automation of FL:** no saving or exporting is automated and no FL call is added.
- **Analysis profile:** stereo exports stay stereo artifacts exactly as exported. There is no fold-down, resampling or widening of the dist10 mono 44.1 kHz int16 profile, and no analysis conversion in this slice.
- **What is proven, and what is not:**
  - The metadata title, the operator attestation and file names do **not** authenticate the open project file or prove how a file was exported. A hash establishes file identity only.
  - Unchanged faders do not imply unchanged audio, and the master is not assumed to equal the sum of the stems.
  - Matching state readings at the checkpoints establish **observed agreement**, not that nothing moved during the export.
  - The timeline is declared, and physical alignment is not verified.
- **Layering:** core records are pure and immutable, ports are protocols plus validators, and the core assessment is pure. The importer is an adapter module that is a listed client of `adapters.audio_io`.
- **Checks:** `uv run --locked …`, plus mutation probes reverted byte-identically.

## Decisions for review

1. **The importer lives in `adapters/rendering/export_handoff.py`**, a listed client of `adapters.audio_io` (`locations`, `integrity`, `limits`) under ADR-028's exact-edge exception (ADR-033). It needs the rendering adapter's workspace allocation and revalidation, which rule 3 forbids another adapter to import. It does not touch `gain_mix`, `run_renderer` or `wav_output`.
2. **Candidate evidence is a new record, `DawImportVerified`, built only by the pure core assessment, not an `ExecutionReceipt`.** Building a `CANDIDATE_READY` receipt from it, and acceptance, stay with coordinator integration.
3. **State continuity is anchored in the FL command journal.**
   - A new recovery-port method, `state_anchor(source_id)`, returns a journal-backed `DawStateAnchor`.
   - The request carries the anchor taken at preparation.
   - The continuation re-queries it immediately before and immediately after the file import, and the core assessment requires all three to agree (see below).
   - The importer adapter never talks to the DAW: file verification and continuity are separate steps, combined only in core.
4. **The timeline is declared, physical alignment is unverified.** Rate, channels and frames are verified by the shared verifier. The timeline origin and sample alignment are **not** verifiable from the files, and the outcome records exactly that.
5. **Workspace identity is file-system identity.** Preparation records the directory's and the request file's `(st_dev, st_ino)`, which on Windows NTFS are the volume serial and file index, and the request file's size and SHA-256. Continuation requires all of them to match, so a replaced directory or file is refused even when its bytes are copied.
6. **Exact coverage and explicit replacement.** Every required output appears in the manifest exactly once and replaces exactly one named before-artifact of the same kind and tracks. The master is required.

## State anchor and continuity

```python
DawStateBasis(Enum): CONFIRMED_WRITE | CONFIRMED_RESTORE | RESOLVED
DawStateAnchor(source_id: str, basis: DawStateBasis, source_seq: int,
               execution_id: ExecutionId, binding_reference: VersionRef, profile_reference: VersionRef,
               conversion: VersionRef, reading: DawFaderReading,
               reservation_watermark: int,   # highest RESERVED seq in the ledger at query time
               ledger_watermark: int)        # highest ledger seq of any kind at query time
```

**`DawRecoveryController.state_anchor(source_id) -> PortResult[DawStateAnchor]`** is the new method. The FL adapter computes the anchor from its journal:
- **The source** is one of:
  - a confirmed apply (`DawWriteConfirmed`; basis `CONFIRMED_WRITE`; reading `after`; seq = its reservation);
  - a confirmed restoration (`CONFIRMED_RESTORE`; reading `after`);
  - a `RESOLVED` entry (`RESOLVED`; its resolved reading; seq = the resolution's).
- **Refused sources** (`PortFailure(INVALID_INPUT)`): an off-target, uncertain, refused or unknown source.
- **IDs and watermarks:** `execution_id`, the binding and the profile come from the source's journaled request (for a resolution, from its root's). The watermarks are read in the same read transaction.

**Valid for export** (checked by the pure core rule `check_state_anchor`, and on construction of `DawExportRequest`):
- `reservation_watermark == source_seq` for a write source, or `< source_seq` for a resolution. In other words, **no write was reserved after the evidence**;
- the anchor's execution, binding and profile references equal the request's;
- its conversion is not blocked (supplied as `DawControlState`).

**Continuity**, assessed by `assess_daw_import` in core:
- The continuation supplies two `DawContinuitySnapshot(anchor: DawStateAnchor, inspection: DawInspection, control_state: DawControlState)` values: `before_import`, taken immediately before `import_export`, and `after_import`, taken immediately after it.
- **Each snapshot** must satisfy all of these:
  - the anchor equals the prepared anchor in every field except the watermarks;
  - **both watermarks equal the prepared anchor's**, so no ledger entry of any kind was appended, which means no reservation, charge, reconciliation or resolution happened in between;
  - `control_state.unresolved` is empty and the anchor's conversion is not blocked;
  - the inspection answers the request's binding and profile, matches the identity, reports the tested build, and reads **exactly** `anchor.reading` in both coordinates.
- **What it establishes:** observed agreement of the journal and the fader at preparation, before the import and after it. It does **not** establish that nothing moved during the export: a manual move and move-back, or a change to routing or plugins, is undetectable by this rule. This limit is stated in records, the ADR and reports.

## Alignment

- **Verified** by `check_resolved_wav_integrity` against each artifact's declared metadata: sample rate, channel count and frame count. Additionally, the outputs must share one sample rate and frame count (`DIMENSIONS`).
- **Declared, not verified:** the timeline origin. Every imported artifact is given the request's declared `timeline.start_seconds`. That value is provenance from the request, and no check of it is claimed.
- **Recording:** `DawImportVerified.alignment = DawAlignmentStatus.DECLARED_TIMELINE_PHYSICAL_UNVERIFIED`, the only value in this slice. A later slice may add timing evidence such as markers or cross-correlation against the baseline exports, with a predeclared method.
- *Superseded:* revision 1's "different timeline start → `ALIGNMENT`" rejection is removed. It was tautological, and the `ALIGNMENT` code is dropped.

## Workspace identity

**`prepare` records** the following in `DawExportAwaiting`:
- `workspace_directory`: the allocated directory, resolved;
- `directory_identity = (st_dev, st_ino)` of that directory;
- `request_file_identity = (st_dev, st_ino)`, the size and the SHA-256 of `export-request.json`.

These are read from `os.stat` (`follow_symlinks=False`) on the directory and `os.fstat` on the open request file. On Windows, `st_ino` is the NTFS file index, and the reparse attribute is checked through `st_file_attributes`.

**`import_export` verifies:**
- `validate_allocated_workspace`: the path is still within policy;
- **then** the directory's `lstat` identity equals `directory_identity`, and it is neither a link nor a reparse point;
- the request file's identity, size and hash equal the recorded ones, read by the file rules below.

**Refusal:** a mismatch gives `WORKSPACE_REPLACED`; a policy failure gives `WORKSPACE_CHANGED`.

**Limits, documented:** identity values can be reused after deletion; the checks are not race-free; they detect replacement, not every concurrent modification.

## Records (`core/records/daw_export.py`, new)

**Edges:** `identifiers`, `invariants`, `artifacts`, `project`, `daw`, `daw_control`, `daw_recovery`.

```python
DawDependencyStatus(Enum): DECLARED | UNKNOWN
DawRoutingDeclaration(reference: VersionRef, status: DawDependencyStatus,
                      dependent_track_ids: tuple[TrackId, ...], effect_notes: tuple[str, ...])
DawExportFormat(container: str, subtype: str)              # exact libsndfile names, e.g. ("WAV", "PCM_24")
DawExportTimeline(start_seconds: float, sample_rate_hz: int, frame_count: int)   # declared
DawExportOutput(artifact_id: ArtifactId, replaces: ArtifactId, kind: ArtifactKind,
                source_track_ids: tuple[TrackId, ...], channel_count: int, file_name: str)
DawExportRequest(export_id: str, execution_id: ExecutionId, project_id: ProjectId,
                 before_checkpoint: ArtifactCheckpoint, candidate_revision: Revision,
                 binding: DawTrackBinding, profile_reference: VersionRef, anchor: DawStateAnchor,
                 routing: DawRoutingDeclaration, outputs: tuple[DawExportOutput, ...],
                 timeline: DawExportTimeline, format: DawExportFormat, render_spec: VersionRef,
                 workspace: ExecutionWorkspace)
DawFileIdentity(device: int, index: int)
DawExportAwaiting(request, request_fingerprint: str, request_size: int, workspace_directory: str,
                  directory_identity: DawFileIdentity, request_file_identity: DawFileIdentity)
DawExportPreparationFailure(export_id, code: DawExportPreparationCode, explanation)
DawExportedFile(artifact_id: ArtifactId, file_name: str, content_hash: str)
DawExportManifest(export_id, request_fingerprint, files: tuple[DawExportedFile, ...],
                  attested_by: VersionRef, attested_at_utc: datetime, attestation: str)
DawContinuitySnapshot(anchor: DawStateAnchor, inspection: DawInspection, control_state: DawControlState)
DawImportRequest(awaiting: DawExportAwaiting, manifest: DawExportManifest)   # files only; no DAW inputs
DawImportedFileEvidence(artifact_id, verified_sha256, container, subtype, finite_samples: bool,
                        file_identity: DawFileIdentity)
DawFilesVerified(request: DawImportRequest, evidence: tuple[DawImportedFileEvidence, ...])   # adapter result
DawAlignmentStatus(Enum): DECLARED_TIMELINE_PHYSICAL_UNVERIFIED
DawImportVerified(request: DawExportRequest, candidate_artifacts: tuple[AudioArtifact, ...],
                  replacements: tuple[tuple[ArtifactId, ArtifactId], ...],   # (output, replaced)
                  evidence: tuple[DawImportedFileEvidence, ...], manifest: DawExportManifest,
                  before_import: DawContinuitySnapshot, after_import: DawContinuitySnapshot,
                  alignment: DawAlignmentStatus)
DawImportRejected(export_id, code: DawImportRejectionCode, explanation)
```

**`DawExportRequest` invariants** (`ValueError`):
- **Identity:**
  - the project matches the checkpoint, binding and outputs;
  - `workspace.execution_id == execution_id == anchor.execution_id`;
  - the anchor's binding and profile references equal the request's;
  - the anchor is valid for export (no later reservation);
  - `candidate_revision` is not among `before_checkpoint.known_revisions`.
- **Replacement:** each output's `replaces` is a member of the before checkpoint with the **same kind and source tracks**. The `replaces` values are distinct, and output IDs are distinct and disjoint from every before-checkpoint ID.
- **Coverage:** `routing.status` must be `DECLARED`; `UNKNOWN` raises `RoutingUnknownError(ValueError)`. The **affected set**, which must be covered exactly, is:
  - the binding's target track stem;
  - one stem per `dependent_track_id`;
  - **exactly one `MIX`** (the master).

  Each must replace the matching before-artifact. An output outside the affected set is refused. Before-artifacts not replaced are retained unchanged.
- **Format and timeline:** every output has channel count 1 or 2, as exported, with no implied conversion; the format is non-blank; the timeline has a positive sample rate and frame count.
- **File names:** each obeys the file rules below.

**`DawImportVerified` invariants:**
- the artifacts correspond one to one, in output order: same ID, kind, tracks and channels; project; `revision = candidate_revision`; the declared timeline; and a hash equal to the evidence's `verified_sha256`;
- every `finite_samples` is true;
- `replacements` equals the outputs' mapping;
- both snapshots satisfy the continuity rule;
- `alignment` is the declared-unverified value.

`DawImportRejectionCode` = `REQUEST_MISMATCH`, `WORKSPACE_CHANGED`, `WORKSPACE_REPLACED`, `STATE_DISCONTINUITY`, `UNRESOLVED_COMMAND`, `CONVERSION_BLOCKED`, `INCOMPLETE_COVERAGE`, `UNEXPECTED_FILE`, `NAME_COLLISION`, `LINKED_FILE`, `DUPLICATE_PHYSICAL_FILE`, `LOCATION`, `HASH_MISMATCH`, `ENCODING`, `DIMENSIONS`, `NONFINITE`, `RESOURCE_LIMIT`, `UNAVAILABLE`.

## File rules

**Output file names**, enforced in records (pure) and again by the adapter:
- exactly one path component with no separators;
- not `.` or `..`;
- not `export-request.json` (reserved), compared case-insensitively;
- no characters invalid on Windows (`<>:"/\|?*` and control characters);
- no trailing dot or space;
- not a Windows reserved device name, with or without an extension (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`9`, `LPT1`–`9`);
- an extension of `.wav`, case-insensitive;
- distinct after Unicode NFC normalization and case folding, since NTFS is case-insensitive.

A collision gives `NAME_COLLISION` (wiring `ValueError` in the record; typed rejection in the adapter).

**Directory listing.** The only entries allowed are the reserved request file plus exactly the outputs' names, compared under NFC and case folding. Anything else gives `UNEXPECTED_FILE`. A listed name that differs only in case from the declared one is `NAME_COLLISION`.

**Every file object**, the request file **and** each WAV, before content is read:
- `os.lstat`: a regular file, not a symlink, and on Windows without `FILE_ATTRIBUTE_REPARSE_POINT` (`LINKED_FILE`);
- `st_nlink == 1`, so no hard links (`LINKED_FILE`);
- the identity `(st_dev, st_ino)` distinct across all outputs (`DUPLICATE_PHYSICAL_FILE`);
- the path resolved strictly and directly inside the recorded directory (`LOCATION`).

**Request file read:** open read-only, `fstat` the handle and compare identity with the `lstat` result and with the recorded identity, then read at most `request_size + 1` bytes. More bytes than recorded, different bytes, or a different hash gives `REQUEST_MISMATCH`.

**WAV content:** `check_resolved_wav_integrity` with the configured `AudioReadLimits`. It already performs a bounded capture with handle and regular-file checks, and its codes map as before: `HASH_MISMATCH`, `ENCODING` (invalid WAV, or a container or subtype other than `request.format`), `DIMENSIONS` (metadata mismatch), `NONFINITE`, `RESOURCE_LIMIT`, `LOCATION`. The adapter's identity is re-checked after the read; a change gives `LINKED_FILE`.

None of this is race-free, and the docstrings and ADR say so.

## Ports

**`core/ports/daw_export.py`, new:**

```python
class DawExportHandoff(Protocol):
    def prepare(self, request: DawExportRequest) -> DawExportAwaiting | DawExportPreparationFailure: ...
    def import_files(self, request: DawImportRequest) -> DawFilesVerified | DawImportRejected: ...

def validate_daw_export_awaiting(request, result) -> None
def validate_daw_files_verified(request, result) -> None
```

**`core/ports/daw_recovery.py`, addition:** `DawRecoveryController.state_anchor(source_id: str) -> PortResult[DawStateAnchor]` and `validate_daw_state_anchor(source_id, result)`.

**Core assessment** (`core/admission/daw_export.py`, new; pure):

```python
def check_state_anchor(anchor: DawStateAnchor, *, request: DawExportRequest | None = None) -> None   # ValueError
def assess_daw_import(awaiting: DawExportAwaiting, files: DawFilesVerified | DawImportRejected, *,
                      before_import: DawContinuitySnapshot,
                      after_import: DawContinuitySnapshot) -> DawImportVerified | DawImportRejected
```

**Order**, where the first failure decides:
1. a file-level rejection passes through unchanged;
2. `before_import` continuity;
3. `after_import` continuity;
4. otherwise build `DawImportVerified`, with artifacts from the request outputs and the file evidence.

Continuity failures map to `STATE_DISCONTINUITY`, `UNRESOLVED_COMMAND` or `CONVERSION_BLOCKED`. A `DawFilesVerified` on its own is **never** candidate evidence.

## Adapters

**`adapters/rendering/export_handoff.py`:** `FileExportHandoff(root, state, protected_locations, limits)`.
- **Construction:** `state` must equal the request's before checkpoint at `prepare`: `state.audio == request.before_checkpoint` and `state.revision == request.before_checkpoint.revision`. Otherwise `prepare` returns `REQUEST_STATE_MISMATCH`.
- **`prepare`:**
  1. allocate the workspace (with protected locations);
  2. write `export-request.json` exclusively with canonical JSON and `fsync`;
  3. record the identities, size and hash;
  4. return `DawExportAwaiting`.

  Its codes are `WORKSPACE_UNAVAILABLE`, `WORKSPACE_OVERLAPS_PROTECTED`, `REQUEST_NOT_PERSISTED` and `REQUEST_STATE_MISMATCH`.
- **`import_files`:**
  1. revalidate the workspace policy and identity;
  2. read and compare the request file;
  3. check that the manifest cites that fingerprint and request;
  4. apply the listing rules;
  5. apply the per-file object rules, then the shared verifier, then the format;
  6. check that rate and frames are equal across outputs.

  It returns `DawFilesVerified` and writes nothing.

**`adapters/fl_studio`:** the controller adds `state_anchor` from the journal: one read pass, the source rules above, both watermarks.

## Tasks (each tests first)

1. **Core records and assessment** (`tests/unit/test_daw_export_contract.py`):
   - every request invariant, including:
     - execution IDs that disagree;
     - the anchor's binding or profile on another reference;
     - an anchor with a later reservation;
     - a replacement of the wrong kind or tracks, a duplicate replacement, a missing master, a missing dependent, an extra output;
     - routing `UNKNOWN`;
     - each file-name rule (reserved name, device names, trailing dot, case and NFC collisions, separators, extension);
   - `assess_daw_import` order and each continuity failure:
     - a watermark advanced by a reservation, and separately by a reconciliation;
     - an unresolved command, a blocked conversion;
     - a fader 1 ulp off in either coordinate, before and after the import;
     - another identity or build;
   - a file rejection passing through;
   - the recorded alignment status;
   - `DawFilesVerified` alone never accepted as evidence;
   - separation from receipts.
2. **FL anchor** (`tests/unit/test_fl_runtime_state_anchor.py`), with the real journal and controller:
   - an anchor for a confirmed apply, a confirmed restoration and a resolution;
   - refusals for off-target, uncertain, refused and unknown sources;
   - watermarks moving with a later reservation and a reconciliation;
   - the binding, profile and execution taken from the journaled request.
3. **Adapter** (`tests/unit/test_daw_export_handoff.py`), with real temporary directories and test-written WAVs:
   - **Preparation:** state matching; exclusive persistence; recorded identities; protected-overlap refusal; originals hashed unchanged.
   - **Import:** stereo success, plus one rejection per code. These include:
     - **a directory replaced at the same path with the original request file copied in** (`WORKSPACE_REPLACED`);
     - a request file replaced by a copy (`WORKSPACE_REPLACED`), or grown or edited (`REQUEST_MISMATCH`);
     - a symlinked or hard-linked WAV (`LINKED_FILE`, skipped where the platform cannot create one, with the skip reason);
     - one physical file listed under two names (`DUPLICATE_PHYSICAL_FILE`);
     - a case-only collision in the directory (`NAME_COLLISION`);
     - a stray file;
     - PCM_16 instead of PCM_24, mono instead of stereo, 48 kHz instead of 44.1 kHz, frames short by one, NaN float samples, a resource limit.
   - **No writes and no conversion.**
4. **Architecture:**
   - inventories for the new records, ports and admission modules, and the new `state_anchor` method;
   - `export_handoff`'s exact shared-audio edges, its rendering-internal edges (`workspaces`, `revalidation`) and no other adapter;
   - the FL controller's edges unchanged except the new core records;
   - public-contract pins (additions only).
5. **Mutation probes**, each reverted byte-identically:
   - accept a fader within tolerance;
   - ignore a watermark advance;
   - skip the after-import snapshot;
   - accept a replaced directory with a copied request;
   - ignore stray files;
   - ignore case collisions;
   - follow a symlinked WAV;
   - accept duplicate physical files;
   - skip the request size bound;
   - accept a subtype mismatch;
   - allow routing `UNKNOWN`;
   - treat `DawFilesVerified` as candidate evidence.
6. **Documentation:**
   - **ADR-033 (Proposed):** the export handoff boundary, the journal anchor and observed-agreement continuity, the declared and unverified timeline, workspace identity, file rules, the new `audio_io` client edge and provenance limits.
   - **Other docs:** index row; overview; engineering-conventions layout line; research log; this plan's outcome.
   - **Locked checks:** focused and full suites, ruff, CLI help, `uv lock --check`, `git diff --check`.

## Later coordinator responsibilities (not in this slice)

- **Preparation:** obtain `state_anchor` after a confirmed apply or restoration or a resolution, build the request, `prepare` it, and record the awaiting status in the run journal. Then stop.
- **Explicit continuation:**
  1. take the `before_import` snapshot (`state_anchor` + inspect + `control_state`);
  2. call `import_files`;
  3. take the `after_import` snapshot;
  4. call `assess_daw_import`.
- **After a verified import:** build the `CANDIDATE_READY` receipt and candidate checkpoint, run analysis under a declared measurement profile (any mono conversion as a separate, versioned derived artifact), verify, and accept or recover.
- **A rejected import:** no candidate exists; continue with a new controlled attempt or recovery.

## Open questions

- The export container and subtype the owner's FL projects produce. Declared per run, never assumed.
- Whether baseline exports must come from the same export settings and timeline. This is likely a verification-time check.
- An independently checkable timing method for a later slice: what evidence would support a physical alignment claim, and with which predeclared tolerance.
- How the operator's routing and effect dependency declarations are captured (a checklist per routing graph). This slice only represents and enforces them.
- Under the fail-closed anchor rule, a write the device refused (reserved, nothing written) supersedes the earlier anchor, so no export can be anchored until a new confirmed write or resolution exists. Whether a device-refused write should leave an anchor current needs a separately reviewed argument.

## Outcome (2026-11-18, implemented tests-first; uncommitted for review)

**Implemented as planned with the revision 3 amendments:**
- **Core:** `core/records/daw_export.py` holds the records and the rules their constructors call. `core/admission/daw_export.py` holds `assess_daw_import` only. `core/ports/daw_export.py` defines `DawExportHandoff`.
- **Recovery port:** `DawRecoveryController` gains `state_anchor` and `continuity_snapshot`, with their validators.
- **FL adapter:** `SqliteCommandJournal.state_anchor` (one locked read pass), the controller's two methods, and the journaled binding, profile and execution loaders in `serialization`.
- **File adapter:** `adapters/rendering/export_handoff.py` (`FileExportHandoff`, `export_request_bytes`), a listed ADR-028 client of `audio_io` (`locations`, `integrity`, `limits`).

**Tests, written before the code they cover:**

| File | Tests | What they cover |
| --- | --- | --- |
| `test_daw_export_contract.py` | 96 | records and assessment |
| `test_fl_runtime_state_anchor.py` | 15 | real journal and controller against device v2 on a fake host, including a reservation, and separately a local-refusal marker, inserted between the first anchor query and the inspection |
| `test_daw_export_handoff.py` | 33 | real temporary directories and synthetic WAVs, including the replaced directory with the request file copied, and separately moved, into it |

**Mutation probes.** Eighteen, each reverted byte-identically and caught:
- the twelve planned in task 5;
- returning a snapshot whose journal changed;
- comparing the binding by reference only, and the profile by reference only;
- anchoring a superseded source;
- anchoring while a command is unresolved;
- skipping the request file's handle-identity check.

The handle-identity probe first survived. A test that replaces the file between its `lstat` and its `open` was added, and the probe was then caught.

**Not done here:** coordinator integration, receipts and candidate checkpoints, analysis conversion, live calls, installation, saving or exporting.

## Review fix (2026-11-18, after Codex review of the implementation)

**Finding.** An in-place WAV change after verification passed. The post-read check compared file identity and link count only. Codex changed one byte of a disposable WAV immediately after its verification, without replacing the file; the import returned `DawFilesVerified`, and the assessment built `DawImportVerified` carrying the old hash.

**Fix** (`adapters/rendering/export_handoff.py`):
- **After each file's read:** its identity, size, modification and change timestamps, link count, mode and file attributes must equal what was observed before the read.
- **Before returning:** the request file and every output are re-checked against those observations, so an earlier file changed during a later file's read is caught.
- **Code:** any difference is `WORKSPACE_CHANGED`. The limitation stays documented: the checks are not race-free, depend on timestamp granularity and on writers not resetting timestamps, and miss a change after the final re-check.

**Regressions, failing before the fix.** The tests backdate the files' timestamps by 10 s so that an in-place write changes them on any clock.
- a one-byte in-place change right after that file's verification (same identity, size and link count), refused at once with no later file read;
- an earlier file changed during a later file's verification;
- an in-place change of the request file during the import.

**Mutation probes.** Four new, each reverted byte-identically and caught:
- skipping the final re-check;
- reducing it to identity only;
- reducing the per-file check to identity only, with and without the final re-check.

The last probe first survived, because the final re-check also rejects the change, only later. It was caught after the fail-fast assertion was added. All 22 probes of this slice are caught.

The conservative anchor rule is kept: any later reservation, including one the device refused, supersedes an anchor. The owner judged it reasonable for this slice.
