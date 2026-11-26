# HT01 calibration design â€” 2026-11-25

Status: owner approved the design and calibration-only implementation on 2026-11-25. Implementation is uncommitted for review; installation and live writes are not authorized by that approval. Extends the [preparation plan](2026-11-25-ht01-calibration-preparation.md). This document supersedes its unknown-start assumptions, not the old calibration.

## Observed starting point

The operator created the disposable copy and confirmed insert 5 / Bass. Generic inspect `ht01_inspect_20261125_03` returned ok on 25 November at 10:17:07 UTC: title `HT01 Live Calibration v001`, Producer Edition v26.1.7 build 5654, API 45, normalized 0.9056249996647239, dB 2.982499599456787. Evidence: `C:/uni/Bachelor-Thesis/test/HT01-live-calibration-v001/inspect-20261125-03.jsonl`.

Two earlier requests timed out at the client. FL script output separately showed project-title mismatch for the first and ok for the second. After operator routing adjustments the third round trip succeeded; this does not isolate the timeout cause conclusively. No gain write was sent. Metadata identity does not authenticate the open file path.

## Proposed scope and points

New plan ID: `ht01-mixer-cal-v1`. Exact project/insert/build/API above. Proposed operating range: normalized [0.78, 0.92]. This includes the observed start and the hypothesized targets of cuts from 0.1 to 3 dB. The curve remains a hypothesis for this scope until tested; no production permission follows from this proposal.

Primary points, in this fixed order: 0.78, 0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.91, 0.92.

For each primary point p, perform the four-target block [0.76, p, 0.94, p]. Concatenate blocks in the listed order: 36 logged writes, including 18 primary visits and 18 staging visits. Each primary is approached once from below and once from above. Staging readings are descriptive only; they do not expand the operating range. The sequence deliberately uses a simple reproducible order rather than a randomization search. It tests direction repeatability, but does not separately estimate time drift or perceptual effects.

Playback must be stopped throughout. The staging positions temporarily exceed the proposed operating range and are explicitly calibration-only permissions. They must never authorize normal agent writes. No processing, routing, automation or other instrument control changes.

Before implementation is accepted, pin a canonical plan digest covering exact scope, ordered targets, primary/staging roles, hypothesis version, tolerances and restore rule. Do not reuse the trap plan digest. The fresh initial reading is runtime evidence, not part of the static plan digest. Clarified by the second review below: the digest binds the declared hypothesis version and restore rule as labels; the protocol file hash separately pins their implementation (`h_db`) and the starting value `HT01_START`.

## Criteria fixed before any run

- Every staging and primary write stores its normalized target within 0.000001; all required replies must validate against their commands.
- At every primary visit, absolute error against H(v) = 20*log10((11**(1.25*v)-1)/10), evaluated at normalized read-back, is at most 0.01 dB.
- The two primary visits agree within 0.000001 dB.
- A fresh initial inspect must exactly equal the recorded starting reading above; otherwise stop and review the changed state, never silently replace the target.
- Planned restore targets that initial normalized value exactly. Final inspect must equal the initial normalized AND dB values exactly.
- Evaluate full sequence, scope, IDs, chained starting readings, reply consistency, replay consistency and restore before accepting the curve. Missing/invalid evidence cannot establish conversion.
- A failed curve or restore means HT01 remains unsupported. No tolerance tuning, automatic fallback or range enlargement.

## Small software increment, separately reviewed before installation

Use existing FL adapter/device responsibilities. Preserve frozen research scripts byte-identically, old protocol bytes/behaviour and old supported scope. Add only the new pinned calibration plan plus a non-frozen runner/evaluator helper. Do not enable HT01 production set_gain_db or restore_gain yet. Exact calibration restoration needs its own narrowly checked plan step, rather than pretending the old production scope applies.

Tests first: wrong scope/build/target/order/digest, stopped transport and start checks, journaling pending before dispatch, identical retry/conflict, post-write uncertain results, malformed/timeout replies, exact restore, invalid/incomplete evidence and MIDI cleanup ownership. A cleanup failure retains ownership and stops further operations. Test the existing trap behaviour and frozen hashes too.

The runner must inspect before writing, chain each step from actual read-back, journal every attempted write before dispatch, and stop after an unknown outcome. Do not issue a speculative restore after an unknown result. Recovery requires a separately reviewed inspect/reconciliation procedure; any live authorization must explicitly cover that procedure and the normal planned return to start. A known pre-write refusal stops probes; return-to-start is allowed only with known state and intact transport ownership.

Installation, reload and the live run remain separate authorizations, with backup and hashes. No provider is involved. No new general registry, adapter or coordinator path is proposed.

## After accepted calibration

Review the captured evidence before enabling a distinct HT01 production profile. Then prepare native state-matched baseline exports and settle the producer-approved acceptance objective. Calibration establishes only fader conversion/repeatability, not masking validity, musical improvement or a target derived from the hidden reference.


## Implementation outcome (2026-11-25; review checkpoint)

- Runtime protocol and its byte-identical device copy add the HT01 probe plan, without changing the trap conversion scope or old command bytes. HT01 commands carry the plan digest. The static plan excludes the fresh initial reading; its separate operational precondition is the captured exact reading above.
- Plan SHA-256: `c7bcbf4aae3196d6235ffec995c8e0da7481ec9ace9ce8cdc1b31a6838358b39`. A literal test pins it, including scope, order, roles, hypothesis and criteria.
- Device v2 checks exact build/API, ordered progression and same-session restoration. A failed/uncertain attempted write poisons this calibration session before the setter: no further probe or restore is allowed. Identical retries replay without another write. Reload loses history and does not authorize recovery.
- New adapter-local `calibration.py` sends one initial inspect, 36 probes, the normal exact restore and a final inspect. It exclusively creates the log and flushes/fsyncs each pending write before dispatch. It records replies, diagnostics and stop/completion decisions. There is no automatic retry or resume. A known refusal after applied steps allows the planned restore only while state remains known; any unknown stops further writes.
- Its evaluator validates the complete header, command IDs, pending/outcome sequence, exact reading chain, scope/build, reply arithmetic, restore and terminal decision before comparing H and repeatability. Duplicate/replayed records are refused by this one-shot evaluator: the runner never retries. This conservative restriction replaces accepting faithful replay records in this new helper only; old tools are unchanged.
- New operator helper: `python -m autonomous_mixing.drivers.ht01_calibration`, with separate `run` and `evaluate` commands. `run` requires explicit ports, fresh log, run ID and `--authorize-plan-digest`; this argument is an accidental-run guard, not proof of operator authorization. `evaluate` opens no MIDI. Failed transport cleanup retains ownership and blocks another run in that process. No installation/profile adoption is performed.
- Frozen `scripts/fl_studio/` files remain unchanged. The additive runtime protocol snapshot test is repinned; old command/reply parity tests remain. The architecture inventory lists the adapter-local helper. This extends the existing calibration responsibility, so no new port, registry or architecture ADR is introduced.
- Engineering evidence uses numerical fake-host tests only, not live FL calibration. The initial inspect above remains the only successful live HT01 read in this preparation.

### Review and verification

Independent read-only review found that the first implementation allowed another device write after an uncertain step. Two regressions failed before the fix; the device now latches uncertainty before dispatch and advances only after a validated applied reply. Reviewer recheck found the issue resolved.

Additional adversarial evaluator regressions caught malformed reply objects and an unrelated reply ID; both were fixed before completion. Stop/completion decisions are persisted rather than only printed. A 10ms test deadline was too small on this host; fake-host tests use 5s deadlines, while timeout tests use deterministic clocks.

Calibration tests: 65 passed with warnings as errors. Eleven sequential mutation probes were caught, with SHA-256 byte restoration checked after each run: missing pending record, missing fsync, missing reply-ID binding, tolerance replacing exact chain/restore, disabled curve/repeat criterion, skipped device build/order/uncertainty guard and skipped cleanup block. Clean full suite, run alone after all mutations restored: `uv run --locked --extra agent pytest -q -p no:cacheprovider` — 7,448 passed, 4 skipped in 289.49s. Ruff check and format (323 files), fixture verifier, lock consistency, helper/existing CLI help and diff checks pass. Frozen FL research files match the HEAD checkout byte-for-byte (Git line-ending filters applied). Runtime/device protocol SHA-256 is `e4b8cf81dfb6171fe29d091a8985f981ab8724acd32a00e3893721d796e45d96` for both copies.

### Explicit remaining gate

No installation, script reload, calibration writes or provider calls occurred during implementation. HT01 production `set_gain_db` and `restore_gain` remain unsupported. Before live calibration, review this code and separately authorize installation/reload and the pinned run. The inspect/reconciliation recovery procedure for an unknown calibration outcome is not implemented or authorized here: an unknown result requires stopping and reviewing evidence, never reloading and automatically retrying or restoring. A live-run authorization must settle that procedure first. Runtime profile adoption, native baseline export, trial acceptance objective and integrated agent run are later increments.

### Second review fixes (2026-11-25; uncommitted)

Owner accepted the second independent review and directed fixes for findings 1 and 2 before installation; finding 3 is planned below for separate review. Still no installation, MIDI, provider call or commit.

- Finding 1 (fixed): the runner treated a device-replayed write reply as a fresh write. Reusing a run ID with a new log after a lost restore reply produced `completed` (exit 0) from 37 replays and zero writes. Any write reply with `replayed` not exactly false, in the probe loop or the restore, now stops the run as `replayed_reply` with no further command, including no restore or final inspect. A replayed refusal stops in the same way.
- Finding 2 (fixed): the evaluator compared records with Python equality, so `false`/`0`, `true`/`1` and `5.0`/`5` were interchangeable and `diagnostics` was unchecked. It now validates the logged command itself, compares header, pending, command, readings and terminal records type-strictly (canonical JSON), and requires `diagnostics` to be a list of strings.
- Regressions written first and observed failing: replay at a probe and at the restore (double delivery on the real device handler), same-run-ID rerun after a lost restore reply, and each reported type substitution (step false/true, version true, insert index 5.0, `restored_exact` 1, diagnostics dict/non-string/null).
- Digest scope (clarification, not a code change): `HT01_PLAN_DIGEST` binds the declared scope, order, roles, tolerances, the hypothesis version label `fl-mixer-h125-v1` and the restore rule label `exact_initial_both_coordinates`. It does not bind the implementation of `h_db` or the value `HT01_START`; the pinned runtime/device protocol SHA-256 pins both. The earlier wording "covering ... restore rule" is to be read in that sense.

### Persistent restart guard (finding 3; owner approved with amendments 2026-11-25)

Problem: the current block after a failed cleanup is process-local (`_HELD`), and the device's uncertainty latch is lost on script reload. No host-side record of an unknown outcome survives, so a new `run` with another log name or run ID is refused only by the device's exact-start check.

Smallest proposed guard:

- Storage (amended by the owner): one fixed file for the whole HT01 calibration scope, `%LOCALAPPDATA%/autonomous-mixing-system/ht01/ht01-calibration.guard.json`. Its path does not depend on the plan ID or digest; these are stored inside it. A per-digest file, as first proposed, is superseded: a changed plan digest could otherwise bypass an unresolved earlier run. There is no CLI argument for it, and it is not derived from `--log` or `--run-id`. If `LOCALAPPDATA` is unset, `run` refuses. Tests redirect the module constant only.
- Arming: after pure argument checks, `run` refuses if the guard exists, before opening MIDI. It then creates the guard exclusively (`x`) and fsyncs it, and only afterwards opens MIDI (amended 2026-11-25, superseding the first implementation, which opened MIDI before arming). If arming fails, MIDI is never opened; a partly created guard stays blocking. If MIDI then cannot be opened, nothing existed to send through, so this process removes its own unchanged guard. It contains the plan digest, run ID, absolute log path and creation time. If the guard already exists in any form, including unreadable or empty, `run` refuses before opening MIDI. `evaluate` ignores the guard.
- Becoming blocked: the guard is blocking from the moment it exists. The runner may remove it only in two cases, both after the terminal record is fsynced and the transport closed without error:
  - (a) result `completed` with `restored_exact`, and the evaluator returns `H_accepted` or `H_rejected` for that log;
  - (b) a stop at the initial inspect: the runner's in-memory result is `stopped/initial_inspect` and the log is exactly that run's header, the one initial-inspect exchange and the matching decision, so no write was ever dispatched. (Tightened 2026-11-25 from "contains no `pending` record", which a damaged or shortened log could satisfy.) The inspect exchange must itself be a shape the runner writes and must justify the stop. With no reply (`timeout`, `invalid_reply`, or `unknown` after a transport error), `reply` is null. Otherwise `replied` carries this command's structurally valid, consistent reply, which fails the start check (not ok, other scope or build, or not the exact start). An exact-start ok reply contradicts the stop.
- Everything else leaves it blocked. That covers unknown outcome, uncertain, replayed reply, restore not confirmed, restore mismatch, a refusal after applied steps, an exception, an invalid log and failed MIDI cleanup.
- Crash and cleanup: a crash, kill or power loss at any point leaves the guard in place, because removal happens only on the explicit success paths above. A crash between arming and log creation leaves a guard whose log is missing. That is treated as unknown (MIDI dispatch cannot be disproved), not as case (b). A failed `transport.close()` keeps the guard even after a completed run. The in-process `_HELD` block stays as well. A `TransportCleanupError` from partial MIDI construction (an `OSError` subclass meaning resources may still be held) is not an ordinary open failure. Its partial transport is retained in `_HELD`, the guard stays, and later runs are blocked in this process and, through the guard, in new ones.
- Script reload, a new FL session, a new log name, a new run ID or a new process never clears it.
- Evidence to clear (manual, owner decision; no software override, recovery writer or general recovery framework):
  - the guard and its log, both hash-recorded and intact;
  - the evaluator output;
  - a documented reconciliation of every pending command whose outcome is not a validated fresh reply in the log;
  - an exact read-only inspect taken after that reconciliation.
- Neither an FL console line nor a current fader reading alone establishes what happened to a pending command.
- Clearance semantics (amended by the owner): manual clearance authorizes a new attempt; it does not prove what happened previously. The interrupted run stays incomplete and is never accepted or stitched to another log. Before moving the guard, the owning process must have exited, the guard and log must be preserved with recorded hashes, and the owner's decision must be recorded in the research log. The guard is then moved into the evidence folder, not deleted. A later recovery tool would need its own review before it could clear the guard.
- Limits: this is an accidental-restart guard, not a security control. Deleting the file or using another OS account bypasses it.

Implementation outcome (2026-11-25, uncommitted): implemented tests-first in `drivers/ht01_calibration.py` with the owner's amendments and the conservative release rules above. 15 guard regressions failed first. All six guard mutation probes were caught. The clean full suite passed alone: 7,476 passed, 4 skipped. Details are in the research log.

### Recovery draft status

The inspect/reconciliation recovery procedure stays a draft for owner approval, not implemented and not authorized. Constraints added by this review:

- An FL Script output line or a current fader reading alone cannot establish a pending command's outcome.
- An identical retry is a possible write unless device-side handling is independently established, so it is not a read-only step.
- Script reload clears only device-session state (latch, replay history, progress). It never clears host-side uncertainty, and it removes the only software restore path for HT01.


## Committed checkpoint and operator-procedure proposal (2026-11-26)

Implementation and review fixes are committed at `f55111c` (`feat: add guarded HT01 calibration runner and evidence validation`). Earlier 'uncommitted' labels above describe their historical review checkpoints. Owner reported the latest isolated full suite as7,524 passed/4 skipped; Codex independently ran141 focused calibration tests after the last fixes. Two earlier full-suite failures have no retained identities and remain unexplained; they are not presented as resolved by the later passing run.

The [operator procedure](2026-11-26-ht01-calibration-operator-procedure.md) proposes the minimum stop-and-review path for uncertainty, without a recovery writer. It preserves the guard/evidence, permits only a separately authorized fresh read once MIDI ownership is settled, and distinguishes current readings from command history. Procedure review, installation/reload/read-only inspection, live calibration, and any exceptional recovery/clearance remain separate decisions. No live action was taken while writing it.


## Live HT01 calibration result — 2026-11-26

Owner explicitly authorized the pinned36 probes and planned exact restoration after v2 installation, operator reload/routing and the successful fresh inspect. Ran ht01_live_20261126_01 through the committed helper with a fresh log and the pinned plan digest. The helper exited0/completed/restored_exact true. No provider, save/export or production-profile change was requested.

Evidence outside the repository: C:/uni/Bachelor-Thesis/test/HT01-live-calibration-v001/calibration-live-20261126-01.jsonl (78 records, SHA-256 8c6280c9504a514728b2d80471aac113c52cce0100b66e9d1ee6fb18c408bb84); separate calibration-live-20261126-01-evaluation.json records the reproduced evaluator result, installed hashes and observed final guard state. All37 pending writes precede their outcomes; all36 probes plus the restore have fresh applied replies (replayed false).

Evaluator verdict H_accepted, no record problems. Maximum primary H error1.8734624829264135e-6dB (limit0.01), repeat difference0dB (limit1e-6), maximum stored-target error4.0978198612151573e-10 normalized (limit1e-6). Restore and final inspect exactly equal initial normalized0.9056249996647239 / dB2.982499599456787. Guard absent after successful evidence validation and clean MIDI closure. Installed v2 pair hashes still match installation evidence (f03cf509… /e4b8cf81…). The script console output was not captured during the run; claims above are from recorded requests/replies and the helper result.

Accepted scope is only HT01 Live Calibration v001,5/Bass, Producer Edition26.1.7 build5654/API45, proposed normalized range0.78–0.92. Staging0.76/0.94 does not widen it. This establishes fader conversion/repeatability evidence, not masking validity or better mixing. No uncertain outcome or recovery procedure was exercised live. HT01 production set_gain_db/restore_gain remain unsupported until a separately reviewed profile increment. State-matched native baseline exports, acceptance objective and first integrated agent trial remain future work.
