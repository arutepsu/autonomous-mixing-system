# HT01 calibration operator procedure — 2026-11-26

Status: proposed operator procedure for review. Code checkpoint `f55111c` is committed and the working tree was clean when this document was started. This document authorizes no installation, reload, MIDI operation, fader write, provider call or manual guard clearance.

Scope: the [approved calibration design](2026-11-25-ht01-calibration-design.md), not an agent trial. This is a stop-and-review procedure, not a new recovery implementation. No additional recovery writer is needed for the first calibration attempt.

## 1. Before installation (read-only preparation)

Record the committed checkpoint, device/protocol hashes, current installed script identities and the new evidence directory. Keep backups outside FL Studio Hardware folders. Preserve the original HT01 project and source samples. The device v2 protocol must match the repository runtime protocol byte-for-byte. Frozen research scripts stay unchanged.

The disposable copy must remain `HT01 Live Calibration v001`, insert5 / Bass, FL Producer Edition26.1.7 build5654/API45. The pinned initial reading is normalized0.9056249996647239 and dB2.982499599456787. Do not move the fader to make it match. A mismatch requires investigation before a write.

The plan digest is c7bcbf4aae3196d6235ffec995c8e0da7481ec9ace9ce8cdc1b31a6838358b39. The proposal covers36 probe writes, the normal planned return to the exact start, and a final inspect. It does not cover an additional restoration after uncertainty.

## 2. Installation and reload (separate authorization)

After explicit installation authorization, back up the installed files with recorded hashes. Install the reviewed v2 script and its exact protocol copy into its own Hardware folder; preserve the v1 folder. The operator selects only the intended controller on Cmd input, with Reply output on its linked port, and reloads v2 manually. Do not run two controllers on the command input or link multiple outputs to that port.

Verify the loaded v2 header and exact port routing. Perform a separately authorized read-only inspect into a fresh log. A title match is metadata identity only, not proof of FLP file path. Confirm stopped playback and preserve all processing. No calibration is dispatched merely because installation/inspection succeeded.

## 3. Normal calibration (separate live-run authorization)

The owner authorizes the pinned36 probes plus their planned exact restoration on the disposable copy. Use the operator helper with explicit ports, a unique run ID and unused log path. The fixed scope-wide guard must be absent legitimately, never removed to make the command run. The helper arms it before opening MIDI.

Keep playback stopped and do not edit or save while the runner operates. After it returns, preserve stdout/exit status, JSONL log and FL Script output. Evaluate the log without MIDI and record its hash and verdict.

Only a complete valid log, accepted numerical criteria and exact restoration can establish HT01 calibration evidence. H_rejected is a completed experiment with a rejected hypothesis, not permission for production writes. No accepted calibration automatically enables a profile or an agent trial.

## 4. On interruption or uncertainty: stop, preserve, inspect only

1. Stop the calibration command. Do not resend an ID, start another calibration, reload the script, move the fader, save/export, or change processing. Leave the guard in place. Do not retry a failed close.
2. Confirm the runner process has exited. If cleanup failed or device ownership is unclear, do not open another MIDI connection until ownership release has been established. An exited process does not establish whether a command wrote.
3. Preserve the original guard and log, stdout/exit status and Script output. Record hashes of files that exist, installed script hashes and any observed reload/project changes. A missing/truncated log or guard is an evidence gap; do not reconstruct it or create a replacement that claims to be the original.
4. Read the log without MIDI. List every pending command and its status: a validated fresh reply, or unknown. A replay, timeout, invalid reply, send exception or missing outcome is not fresh confirmation. Device console text is supporting evidence only. Do not call an unknown command 'not executed'.
5. After read-only inspection is explicitly authorized and transport ownership is settled, send one generic inspect with a new ID and separate log. Compare the exact current identity/build and both fader readings with the saved initial reading and the last commands. This establishes only the reading at inspection time, not the command history or audio state. Do not retry a pending write as an evidence query.
6. Stop for owner review with all evidence. No write follows this procedure automatically, including when the current reading exactly equals the saved start.

## 5. Decision table after evidence review

| Evidence/state | Permitted conclusion and next action |
| --- | --- |
| Complete valid run, exact restore, H_accepted | Review and record calibration evidence. Production-profile adoption remains separate. |
| Complete valid run, exact restore, H_rejected | Preserve rejected result; leave HT01 unsupported. No threshold tuning or fallback adoption. |
| Valid pre-write stop, no write dispatched | Resolve the stated precondition; a new attempt needs owner authorization. Guard auto-release is permitted only by the implemented evidence and cleanup rules. |
| Interrupted run, fresh inspect exactly at saved start | Current reading matches; history remains incomplete. Owner may separately authorize a new attempt after evidence preservation and manual clearance. No interrupted run becomes accepted. |
| Interrupted run, current reading differs or identity/build is unverified | Keep blocked. No restoration or project reopening is authorized by this procedure. Define and authorize a specific recovery action separately. |
| MIDI ownership unresolved, missing/damaged evidence, or unknown file identity | Keep blocked; no further MIDI operation until the relevant uncertainty is resolved. |

A script reload or project reopen never grants authority. Do not rely on reopening the disposable project to restore the fader: that behaviour has not been established. Do not use v1 set_gain to bypass the calibration block, or production restore_gain outside its calibrated scope.

## 6. Manual clearance, if separately approved

Clearance authorizes a new attempt; it cannot certify the old command history. The owning process must have exited. Preserve guard/log and their hashes, including all evidence gaps, evaluator output, per-command findings and the new inspect. Record the owner's specific decision and limitations in the append-only research log before moving the guard into the evidence folder. Never delete it, silently rewrite it or stitch the interrupted log to a later run. If the current reading does not exactly match the saved start, clearance alone does not authorize moving the fader or bypassing the start check.

## 7. Immediate next approval

Review this procedure, then authorize installation/reload and a fresh read-only inspection only. The live calibration is a subsequent bounded authorization after the installation evidence and current starting reading have been checked. No provider is required at any calibration stage.
