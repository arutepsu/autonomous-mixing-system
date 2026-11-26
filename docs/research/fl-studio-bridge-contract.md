# FL Studio command bridge — contract, version 1

**Status: Draft for review (2026-11-18).** Technical feasibility work under the owner-authorized FL Studio trial (ADR-009 unchanged, FR-14 stays withdrawn). This contract lets an external Python process send commands to an FL Studio controller script and read structured replies.

Increments:
- **Inspect** (read only): implemented and **verified live** on 2026-11-18 (see "Live inspect trial").
- **`set_gain`**: one bounded absolute normalized fader write. Implemented, simulated and **verified live** on 2026-11-18: one write, a duplicate replay and a conflicting-ID refusal (see "Live write trial").
  - *Supersedes the earlier statement here that it was not installed or run live.*
- **`probe_gain`** (calibration): one absolute write to a position of a pinned calibration plan, or back to the calibration's start. Specified in [fl-studio-db-conversion-design.md](fl-studio-db-conversion-design.md), section 5, "Pinned implementation".
  - Status: installed and **run live** on 2026-11-18. Calibration run `live20261118codex`: 27 probes, verdict `H_accepted` for 0.62–0.86, exact restore. Recorded in the design's section 7. *Supersedes "not installed or run live".*
- **`set_gain_db`**: one calibrated dB cut, from the starting dB read-back plus the requested change, by conversion `fl-mixer-h125-v1`.
  - Specified and recorded in the design's section 8.
  - Status: implemented and simulated 2026-11-18; **installed and verified live** (see Live dB trial result; supersedes the earlier pending-live status).
  - It keeps every write rule (identity, starting state, pending journal, duplicates, uncertain outcomes, write guard). It adds conversion, scope, range and cut validation, the `applied_off_target` status (a known write that missed its dB), and the client's conversion block.
  - It keeps every `set_gain` check except the cut bounds, and adds plan validation (`plan`) and the restore rule (`restore_target`). The pending-journal, duplicate, uncertain-outcome and write-guard rules all apply.

Gain stays normalized. Verified dB conversion is designed in [fl-studio-db-conversion-design.md](fl-studio-db-conversion-design.md) (implemented and verified live in the calibrated scope). Save/export automation and coordinator integration are later increments. Adopting the bridge as a renderer adapter in `autonomous_mixing` would need its own ADR.

Files:
- `scripts/fl_studio/bridge_protocol.py`: wire encoding, validation, bounds and tolerances, shared by both sides;
- `scripts/fl_studio/device_bridge.py`: FL Studio controller script "Thesis Bridge";
- `scripts/fl_studio/bridge_client.py`: external client, write guard and WinMM MIDI transport;
- `tests/unit/test_fl_bridge.py`: simulated checks.

The gain-trial script (`scripts/fl_studio/device_gain_trial.py`, procedure in [fl-studio-gain-trial.md](fl-studio-gain-trial.md)) stays available unchanged, so the owner can reproduce that trial.

## Evidence the design rests on (checked 2026-11-18)

| Question | Evidence | Consequence |
| --- | --- | --- |
| FL Studio build and API | FL Studio Producer Edition 26.1.7, build 5654, scripting API 45 (live gain trial and live inspect) | `getProjectTitle` (API 38) and `safeToEdit` (API 29) are available |
| Receiving from outside | `OnSysEx(msg)` with `msg.sysex` (bytes) and `msg.handled` (API 1, Image-Line manual) | commands travel as SysEx into a controller script |
| Replying | `device.midiOutSysex(bytes)` (API 1), which sends a complete `F0 … F7` message to the **linked output**: the MIDI output whose *port number* equals the script's input port number in FL's MIDI settings. `device.isAssigned()` reports whether one is linked | replies need a second, linked port |
| Writing and its guards | `mixer.setTrackVolume` (API 1), `transport.isPlaying` (API 1), `general.safeToEdit` (API 29); one absolute write with read-back worked live in the gain trial | `set_gain` uses the same calls and checks as the gain trial |
| Script runtime | FL Studio's embedded Python 3.12.1 (`Shared/Python/`): `json` and `re` are in `python312.zip`, and `math` is built in. The protocol module and the controller script load under that interpreter's `python.exe` (checked outside FL Studio) | the shared protocol module uses only these |
| Python MIDI support | the project environment has no `mido` or `python-rtmidi` | the client uses Windows' `winmm.dll` through `ctypes`; no new dependency |
| Virtual ports | loopMIDI with `Thesis Bridge` (gain trial), `Thesis Bridge Cmd` and `Thesis Bridge Reply` (created by the owner; listed by WinMM 2026-11-18) | two-port bridge as below |
| FL Studio's SysEx size limit | not documented | a conservative pilot limit is set (below) |

## Ports

Two loopMIDI ports, one per direction:

| Port | Written by | Read by | FL Studio setting |
| --- | --- | --- | --- |
| `Thesis Bridge Cmd` | client (WinMM output) | FL Studio (MIDI input) | input enabled, controller type **Thesis Bridge (user)**, port number *N* |
| `Thesis Bridge Reply` | FL Studio (MIDI output) | client (WinMM input) | output port number *N*, the same as the input |

The client opens ports by exact name, overridable with `--out` and `--in`. A loopMIDI port carries what is written to it to every reader, so a single port would echo each command back to the client. The client would then reject the echo as malformed, which is why this contract uses two ports.

## Message encoding

One SysEx message per command or reply: `F0 7D <payload> F7`.
- `7D` is the MIDI manufacturer ID for non-commercial and educational use. SysEx with other IDs is left unhandled by the script and ignored by the client.
- The payload is a JSON object, compact (`,` and `:` without spaces), with sorted keys, ASCII with `\u` escapes. Every payload byte is printable ASCII (0x20–0x7E), so it is a valid 7-bit SysEx data byte. NaN and infinity are not JSON: `encode` refuses them and `decode` rejects them.
- **Size limit:** at most **1024 bytes** per message, framing included (pilot setting, `MAX_MESSAGE_BYTES`).
- **Text limits** count **escaped bytes**:

  | Text | Limit (escaped bytes) |
  | --- | --- |
  | project titles | 128 |
  | insert names | 64 |
  | FL version text | 64 |
  | error text | 64 |

  A non-ASCII character costs six escaped bytes.
  - *Correction:* the first draft counted characters (128 and 64) and claimed that an inspect reply with maximal fields stays well below the limit. That was wrong. A 128-character non-ASCII title alone escapes to 768 bytes, so a maximal command could have exceeded 1024 bytes.
  - The script **clips** observed text (titles and names read from FL Studio) to these limits before replying. It compares identity on the unclipped values.
- **Size budget, tested:** worst cases are built from maximal escaped text, 64-character IDs, 31-bit integers and the longest float representations. A `set_gain` command, an `applied` reply, an `uncertain` reply with a maximal error, and an inspect `ok` reply all fit within 1024 bytes.
  - The trial's `set_gain` command, with a 32-character ID: 240 bytes.
  - The live inspect log: commands 138–157 bytes, replies 275–310 bytes.
- **Protocol version:** `"v": 1` in every message.

### Commands (client → FL Studio)

```json
{"id":"1c3d388b51814d45a116e384acf0ce59","insert":{"index":2,"name":"Bass"},"project_title":"Thesis Bass Bridge Test","type":"inspect","v":1}
{"expected":{"db":-0.574523,"normalized":0.78},"id":"…","insert":{"index":2,"name":"Bass"},"project_title":"Thesis Bass Bridge Test","target_normalized":0.76,"type":"set_gain","v":1}
```

| Field | Rule |
| --- | --- |
| `v` | integer 1 (not `true`) |
| `id` | unique per command: 8–64 characters of `A–Z a–z 0–9 _ -`. The client uses a random UUID4 in hex, or `--command-id` for duplicate and conflict trials |
| `type` | `inspect` or `set_gain` |
| `project_title` | the **expected** project metadata title |
| `insert` | exactly `{"index": int ≥ 1, "name": text}`: the expected insert index and its exact name |
| `expected` | `set_gain` only, required: exactly `{"normalized", "db"}`, the starting gain the client last observed. Finite numbers, not booleans; `normalized` in [0, 1] |
| `target_normalized` | `set_gain` only, required: the **absolute** fader target (never a relative step). Finite, > 0, ≤ 1 |
| fields | exactly those of the type; anything missing or extra is rejected |

**Bounds** (`set_gain`, error code `bounds`):
- `0 < target_normalized` and `expected.normalized − target_normalized` in **(0.002, 0.03]**.
- This is the gain trial's bounded cut (`MAX_NORMALIZED_CUT = 0.03`, lower edge twice the 0.001 tolerance).
- Differences are compared after rounding to 1e-9, so decimal edges such as 0.78 → 0.75 (cut exactly 0.03) behave as written.
- The client checks the bounds before sending; the script checks them again against the *actual* starting gain.

### Replies (FL Studio → client)

Exactly the keys `v`, `id`, `type` (`"reply"`), `status`, `observed` and `error`. `id` is the command's ID, or `null` when no valid ID could be read.

**Identity fields** (both shapes):
- `project_title` and `fl_version` (`ui.getVersion(5)`): clipped text;
- `api`: `general.getVersion()`, an integer ≥ 1;
- `insert`: `{"index", "name"}`; `name` is `null` if the index is not an insert.

**Inspect shape:** the identity fields plus `gain`, which is `{"normalized", "db"}` from `mixer.getTrackVolume(i, 0)` and `(i, 1)`, or `null`.

**Write shape:**
- the identity fields;
- `before` and `after`: each `{"normalized", "db"}` or `null`;
- `db_change`: `after.db − before.db` or `null`;
- `replayed`: `true` only when the reply is a recorded result returned for a duplicate.

| `status` | Command | Meaning | `observed` | `error` |
| --- | --- | --- | --- | --- |
| `ok` | inspect | identity matched; gain read | inspect shape, with gain | `null` |
| `applied` | set_gain | one write made; read-back agrees | write shape: `before`, `after`, `db_change` | `null` |
| `identity_mismatch` | both | project title or insert name differs | the command's shape, no gain | `project_title` or `insert` |
| `precondition_failed` | set_gain | nothing written | write shape, `before` if read, no `after` | `transport_playing`, `edit_unavailable`, `start_gain` or `cut_bounds` |
| `uncertain` | set_gain | the setter was called; its effect is not established | write shape, whatever could be read (after a processing failure: identity and a valid `before` only) | `readback_disagrees`, `exception_after_write:<type>` or `invalid_observation` |
| `invalid_command` | both | decoding or validation failed, or a reused ID with other content | `null` | `framing`, `manufacturer`, `encoding`, `json`, `too_large`, `version`, `field`, `type`, `bounds` or `duplicate_id_conflict` |
| `unsupported` | both | valid type not implemented by this script version (the installed inspect version answers `set_gain` so) | `null` | the type |
| `error` | both | nothing written: API below 38, an exception **before** any write, or an observation the script's own check rejected | `null` | `api_too_old`, `invalid_observation` or the exception class name |

**Reply rules** (`validate_reply`):
- **IDs and observations:**
  - `ok`, `applied`, `identity_mismatch`, `precondition_failed` and `uncertain` must carry the command ID and observations in their shape.
  - Every other status carries `observed` `null`.
  - Text, index and API fields have the types above.
- **Gain values:** finite numbers, never booleans, with `normalized` in [0, 1] (the documented fader range).
- **Status-specific content:**
  - `applied` must have `before`, `after` and a finite `db_change`.
  - `precondition_failed` has no `after` and no `db_change`.
  - `identity_mismatch` carries no gain.
  - `replayed` is a boolean.
- **Errors:** `ok` and `applied` have `error` `null`; every other status names its error.

*Revisions:* (1) after the 2026-11-18 review, the rules no longer accept any `ok` reply with two numeric gain values; (2) the write shape and its statuses were added with `set_gain`.

## Validation

**FL Studio script:**
- It decodes and validates the command (including bounds) before any FL Studio call, then checks duplicates (below).
- **Inspect:** API, then project title, then insert name; the gain is read only after both identity checks pass.
- **`set_gain`:** the checks run in this order, and each refusal writes nothing:
  1. API ≥ 38 (`error` / `api_too_old`);
  2. project title (`identity_mismatch` / `project_title`);
  3. insert index in range and exact name (`identity_mismatch` / `insert`);
  4. transport stopped (`precondition_failed` / `transport_playing`);
  5. `general.safeToEdit()` (`edit_unavailable`);
  6. starting gain within **0.001 normalized and 0.05 dB** of `expected` (the gain trial's `NORMALIZED_TOLERANCE` and `DB_TOLERANCE`; `start_gain`);
  7. cut from the **actual** start within the bounds (`cut_bounds`).
- It then calls `mixer.setTrackVolume(index, target)` **once** and reads normalized and dB back.
- **Read-back** must agree in the gain trial's sense: normalized within 0.001 of the target, and dB lower than before by more than 0.05 dB. Then the status is `applied`, with `db_change` (the achieved dB change) recorded. Otherwise it is `uncertain` / `readback_disagrees`, reporting what was read.
- **Failures after the setter.** From the moment the setter is called, **every** later step runs inside the uncertain-outcome boundary. A failure there is `uncertain`, never `error`, because the setter may have run:
  - the setter itself and the read-back;
  - the `db_change` arithmetic and the read-back rule;
  - the reply's own check and encoding.

  The fallback reply is built so that it cannot fail. Identity comes from the validated command, and `before` is kept if it is valid; no post-write values are included. `handle()` applies the same fallback as a last resort to anything that escapes after a write attempt.
  - *Revised after review on 2026-11-18:* only the setter and the read-back were inside the boundary, so a read-back such as `None` raised `TypeError` in the `db_change` arithmetic and was reported as a definite `error` after the fader had moved.
- **Own check before sending.** The script validates every reply before sending it:
  - an observation that fails the rules before any write becomes `error` / `invalid_observation`;
  - after a write it becomes `uncertain` / `invalid_observation`, with identity taken from the command it had checked.
- Exceptions are never raised into FL Studio's event loop.

**Client.** A reply is **accepted** only if it passes the reply rules, carries the pending command's ID, and is consistent with the command (`reply_consistency`):
- **Status and shape:** the status must be one the command type can produce (an `applied` reply to an inspect, or an `ok` reply to a write, is rejected), and the observation shape must match the type.
- **Identity:**
  - `ok`, `applied`, `precondition_failed` and `uncertain` must report exactly the expected project title and insert;
  - `identity_mismatch` must actually differ.
- **`applied` values:**
  - `before` within the start tolerances of `expected`;
  - `after` at the target within 0.001, with a dB decrease of more than 0.05 dB;
  - `db_change` equal to `after.db − before.db` (to 1e-9).
- **`uncertain`:** reports and claims nothing, so its values are not judged.

Unaccepted replies:
- **Undecodable** messages are recorded as **malformed** (with their bytes) and ignored, because their ID cannot be trusted.
- Decodable replies with **another ID** are recorded as **stray** (if valid) or **malformed** (if not) and ignored.
- A decodable reply with the **pending ID** that fails the rules or the consistency check ends the command as **`invalid_reply`**: recorded as malformed, not accepted, exit code 4. The client does not wait for another answer.
- An accepted refusal or error is returned unchanged; the client never retries on its own.

## Timeout behavior and write guard

- **Timeout.** The client waits **2.0 s** by default (pilot setting, `--timeout`). If no matching reply arrives, the outcome is `timeout`, with the command, elapsed time, and any strays and malformed replies recorded.
- **Inspect timeout:** nothing is known and nothing was changed; resend with a **new** ID.
- **Write timeout:** the write is **uncertain**: it may have been applied with the acknowledgement lost.
- **Write guard.** After a write whose outcome is `timeout`, `invalid_reply` or an `uncertain` reply, the client sends **no further write** (outcome `blocked`, exit code 5, nothing sent). The block clears only when an `inspect` of the blocked project title and insert returns `ok`. An inspect of another insert, or one with an identity mismatch, leaves it in place.
- **Pending writes.** Every write is journaled as **`pending`** *before* it is dispatched, and a pending write blocks like an uncertain one.
  - It is resolved by its own known outcome: an accepted reply with the same ID that is not `uncertain`, including a refusal or a duplicate conflict, since those wrote nothing. An `ok` inspect of its title and insert also resolves it.
  - If the process dies, or the send fails, between dispatch and logging the outcome, the log still holds the pending write, and the next `set-gain` is blocked.
  - *Added after review on 2026-11-18:* the outcome used to be logged only after the reply arrived, so a lost process left no trace of a write that may have reached FL Studio.
- **Reading the gain after an uncertain write:**
  - gain at `target`: the write happened;
  - gain at `expected`: it did not happen;
  - anything else: stop and report.

  The next write then uses the newly observed gain as `expected`.
- **The guard across invocations.** `set-gain` **requires `--log`**. Before every write, the CLI rebuilds the guard from that JSON-lines log (`write_block_from_records`), so the guard holds from one invocation to the next. A write appends a `pending` record before dispatch. Every run then appends its full outcome record: command, reply, strays and malformed replies.
- **Exit codes:**

  | Code | Meaning |
  | --- | --- |
  | 0 | an accepted reply, other than `uncertain` |
  | 2 | timeout, or an `uncertain` reply |
  | 3 | ports missing |
  | 4 | invalid reply |
  | 5 | write blocked |
  | 6 | command refused locally (validation or bounds; nothing sent) |

## Duplicate IDs and reload

The history is kept per script session (`device_bridge._HISTORY`, emptied in `OnInit`). It maps every validated command ID to its canonical payload and, for `set_gain`, its reply.
- **Same ID, identical payload** (`set_gain`): the **recorded reply** is returned again, with `replayed: true` and no call to FL Studio. This holds for every recorded result: `applied`, a refusal, `uncertain` or `error`. A replay never writes.
  - *Refines the earlier specification, which said only "the recorded reply is returned".*
  - Inspect commands are re-read, not replayed, because they are read-only.
- **Same ID, different payload:** rejected as `invalid_command` / `duplicate_id_conflict`, with no write. "Different payload" includes another type, so an inspect reusing a write's ID is rejected, and so is a write reusing an inspect's ID.
- **Reload:** reloading the script (Script output → Reload) or restarting FL Studio **loses the history**.
  - A resent write is then stopped only by the starting-gain precondition. After an applied cut the gain sits at the target, not at `expected`, so the resend is refused as `precondition_failed` / `start_gain`.
  - The history is in memory and unbounded within a session; the trial sends a handful of commands.

## Identity: a documented limitation

The project is identified by its **metadata title** (`general.getProjectTitle()`, API 38: the Project Info title, F11), not by its file.
- Two copies of a project with the same title cannot be told apart, and an unsaved project can carry any title.
- No documented API call returns the project file path; `ui.getProgTitle()` returned only `FL Studio 2026` live. Procedures must therefore give each disposable copy a unique title.
- Insert identity is the index plus the exact insert name. Renaming or reordering inserts between commands can defeat it.
- Clipped observed text could, in an edge case, equal a command's title exactly. That happens only if the command's title is precisely the first 128 escaped bytes of a longer FL title.

## Live inspect trial (2026-11-18, inside FL Studio)

**Evidence:**
- The retained client log: `C:/Users/my_goconut/.codex/.chatgpt-projects/g-p-6ac5f45645e88191bc2efa98d1049408/fl-bridge-live-inspect.jsonl`.
  - Size and integrity: 2435 bytes, 5 JSON-lines records, SHA-256 `82ca7f96e71b72edaa2167e642ff0b0b35dea776be5c613aa5254ae035f2f965`.
  - Last written 2026-11-18 11:39:53 UTC.
  - It is kept outside the repository, in the Codex project folder, and was read, not modified.
- The installed script was the reviewed inspect version: `device_bridge.py` `e78746b8…` and `bridge_protocol.py` `2e2bda86…`, installed 10:00 UTC and unchanged since.
- Project: the disposable copy `bass-bridge-test.flp` (metadata title `Thesis Bass Bridge Test`). The ports and FL Studio's MIDI settings were set up by the owner.

| UTC | Command | Client outcome | Reply |
| --- | --- | --- | --- |
| 11:32:47 | inspect, `Thesis Bass Bridge Test`, insert 2 `Bass` | **timeout** after 2.0 s | none; no strays, no malformed replies |
| 11:34:28 | same, with a 5.0 s timeout | **timeout** after 5.0 s | none; no strays, no malformed replies |
| 11:39:42 | same | **replied** | `ok`: API 45, `Producer Edition v26.1.7 [build 5654]`, insert 2 `Bass`, normalized **0.7800000002607703**, **−0.5745229721069336 dB** |
| 11:39:53 | title `Wrong Trial Title`, insert 2 `Bass` | **replied** | `identity_mismatch` / `project_title`; observed title `Thesis Bass Bridge Test`, gain `null` |
| 11:39:53 | title correct, insert 2 `Wrong Insert Name` | **replied** | `identity_mismatch` / `insert`; observed insert 2 `Bass`, gain `null` |

**What this establishes:**
- The two-port round trip works inside FL Studio.
- Correlation by ID held: every reply carried its command's ID, with no strays.
- The successful read matches the state saved after the gain trial (0.78 / −0.574523 dB).
- Both deliberate mismatches were refused with the observed identity and no gain.

The elapsed time of the replied commands was logged as 0.0 s, which is below the client clock's resolution; no latency is claimed.

**What it does not establish:**
- **The cause of the two timeouts.** The log does not record one: they precede the first successful reply by about seven and five minutes, and nothing in it shows what changed. They count as unexplained setup-phase failures, not as bridge behavior.
- Anything about writes.

## Live write trial (2026-11-18, inside FL Studio)

**Evidence: the retained client log** `C:/Users/my_goconut/.codex/.chatgpt-projects/g-p-6ac5f45645e88191bc2efa98d1049408/fl-bridge-live-write.jsonl`.
- 4701 bytes, 8 JSON-lines records, SHA-256 `3be5fff158af62d61afef55a209c73c14c1ab1c2bb1dc308e3534a1aa23aee7c`.
- Last written 2026-11-18 12:12:37 UTC.
- It is kept outside the repository, in the Codex project folder, and was read, not modified.

The observations below are **attributed to the captured requests and replies in that log**. FL Studio's Script output window was not captured.

**Installed script during the trial:** `Settings/Hardware/Thesis Bridge/`, byte-identical to the repository's reviewed versions (read 2026-11-18):
- `device_bridge.py`, SHA-256 `477139b1a6bb92c73f65c848d5f4bebbb9e2d2c044cdf5feaee9631fb9bb3550`, file time 12:01:45 UTC;
- `bridge_protocol.py`, SHA-256 `901c69af1c815c501d2cb8de1d9a8b024e061e51649cf3d702da6c30b51a6f7d`, file time 11:50:13 UTC.

Both were installed before the trial. Claude Code did not install them, and the log does not record who did. `Thesis Gain Trial/device_gain_trial.py` is unchanged (`93879cf1…`).

**Project:** `bass-bridge-test.flp`, metadata title `Thesis Bass Bridge Test`, insert 2 `Bass`, API 45, `Producer Edition v26.1.7 [build 5654]`. The project file's last save is 09:39:18 UTC (SHA-256 `71a6c6a9…`), before the trial, so the written fader value exists **only in the open, unsaved session**. It was left as observed.

| # | UTC | Request | Client outcome | Reply (as captured) |
| --- | --- | --- | --- | --- |
| 1 | 12:12:08 | `inspect` | replied | `ok`: **0.7800000002607703 / −0.5745229721069336 dB** |
| 2 | 12:12:17 | `set_gain` ID `live_write_20261118_01`, expected 0.7800000002607703 / −0.5745229721069336, **target 0.76** | `pending` (journaled before dispatch) | — |
| 3 | 12:12:17 | same | replied (0.016 s) | **`applied`**: before 0.7800000002607703 / −0.5745229721069336; after **0.7599999997764826 / −1.1527416706085205 dB**; `db_change` **−0.5782186985015869**; `replayed: false` |
| 4–5 | 12:12:28 | **identical** command and ID | `pending`, then replied | `applied`, identical observations, **`replayed: true`** |
| 6–7 | 12:12:37 | **same ID**, target **0.755** | `pending`, then replied | **`invalid_command` / `duplicate_id_conflict`**, no observations |
| 8 | 12:12:37 | `inspect` | replied | `ok`: **0.7599999997764826 / −1.1527416706085205 dB** |

**Re-checked against the repository protocol (2026-11-18):**
- every command and reply validates, and every reply is consistent with its command (`reply_consistency`);
- `db_change` equals after minus before exactly;
- the replay's observations equal the original's except for `replayed`;
- the duplicate's payload equals the original's, while the conflict's differs;
- there are no strays or malformed replies;
- the write guard rebuilt from the 8 records ends clear: each `pending` record is resolved by its own known outcome.

Message sizes: the `applied` reply is 423 bytes, the inspect replies 310 bytes.

**What this establishes:**
- One bounded absolute normalized write through the bridge, with read-back. The fader stored 0.76 as 0.7599999997764826.
- A known, recorded outcome; the client journaled the write as pending before dispatch.
- The same command ID and payload returned the recorded result, marked `replayed`.
- A reused ID with different content was refused with `duplicate_id_conflict`.
- The final inspect matched the single write's target.

**What it does not establish:**
- **That the replay made no second write is not shown by the fader value.** The command is absolute, so a second write of 0.76 would read back the same. The evidence is the reply's `replayed: true`, which the script sets only on its history path, and that path never calls FL Studio; the simulated tests assert that path. The Script output, which logs each reply, was not captured.
- **Reload.** History loss on reload was not exercised live.
- **Uncertain outcomes.** A timeout or an `uncertain` outcome did not occur live, so the write guard's blocking was not exercised live.
- **Persistence.** The written value was not saved, so persistence was not exercised.
- **The intended dB effect.** The cut was a normalized 0.02 (−0.578 dB measured); no dB intent was set.

## What was checked

**Simulated** (`uv run pytest -q tests/unit/test_fl_bridge.py`; 172 checks after the 2026-11-18 post-write and pending-write fixes, 160 after `set_gain`, 78 before it and 42 in the first inspect draft). A fake FL Studio host replays the live values, using a straight-line fake fader curve that is a test stand-in only; a fake transport stands in for the MIDI ports.
- **Encoding and size:**
  - malformed messages: framing, manufacturer, 8-bit and control bytes, invalid JSON, NaN, a non-object, oversize;
  - escaped-byte text limits;
  - the worst-case size budget;
  - clipping of long observed text.
- **Command validation:**
  - inspect fields;
  - `set_gain` fields, with missing, extra, boolean and non-finite values;
  - bounds: target 0, cut 0.002, cut 0.001, cut 0.031, a raise, no change; and both edges accepted (0.0021 and exactly 0.03).
- **Reply validation:** every status and both shapes, with missing, extra and ill-typed fields.
- **Consistency:** of inspect and write replies with their commands, including `applied` values that contradict the command or each other.
- **Inspect:** as before, with no writes.
- **`set_gain` success:** one setter call, with `before`, `after` and `db_change`; a start within tolerance is accepted.
- **Every precondition refusal**, each with zero writes:
  - API too old;
  - title mismatch, insert-name mismatch, insert out of range;
  - transport playing; editing unavailable;
  - starting normalized gain off by more than 0.001 in either direction;
  - starting dB off by more than 0.05 in either direction;
  - the cut from the actual start outside the bounds.
- **Exception before the setter:** `error`, with no write.
- **`uncertain` outcomes:**
  - read-back disagreement (fader elsewhere; no dB decrease);
  - exception in the setter;
  - exception in the read-back;
  - non-finite read-back;
  - read-back values that break the post-write arithmetic (`None` or text for the normalized or dB value), recorded and replayed without a second write;
  - failures in the read-back rule, in the reply's own check and in the reply's encoding;
  - unusable FL version and API values after a write.
- **Duplicates:**
  - same ID and payload: the recorded `applied` replayed with one write in total; a replayed refusal; a replayed `uncertain` that never calls the setter;
  - conflicting reuse: other target, other expected gain, other insert, inspect reusing a write's ID, write reusing an inspect's ID;
  - reload: the history is lost, and the resend is refused by `start_gain`.
- **Client:**
  - set-gain round trip;
  - local refusal of an out-of-bounds write (nothing sent);
  - correlation despite strays (including a stray `applied`) and malformed replies;
  - `invalid_reply` for inconsistent write replies, which also blocks writes;
  - write timeout → blocked → cleared only by an `ok` inspect of the same title and insert;
  - an `uncertain` reply blocks, a refusal does not;
  - the guard rebuilt from log records;
  - a write journaled as `pending` before anything is sent (inspects are not);
  - a failed dispatch left pending and blocked;
  - pending records resolved only by the same write's known outcome or an `ok` inspect, not by another write's reply or a blocked attempt;
  - the CLI refusing a write after a pending record that has no outcome (exit 5);
  - the CLI requires `--log` for writes, refuses a blocked write (exit 5) and an out-of-bounds write (exit 6) before opening ports.

**Live, transport only, outside FL Studio** (earlier on 2026-11-18, single loopMIDI port `Thesis Bridge`, output → input):
- WinMM SysEx looped back byte-exact at 64, 512 and 1024 bytes, and a silent port returned nothing at its deadline.
- The CLI recorded its own echoed command as malformed and reported `timeout` (exit code 2).
- Missing ports gave exit code 3.

**Inside FL Studio:**
- the inspect trial above;
- `set_gain`: the live write trial above (one write, duplicate replay, conflicting-ID refusal).

## Live dB trial result (2026-11-18)

Installed and verified live: requested −1 and −2 dB cuts measured −0.999595 and −1.999212 dB; duplicate replay, conflicting-ID refusal and local range refusal behaved as planned. Final inspect exactly matched the starting normalized and dB readings. This supersedes the earlier pending-live status. Detailed evidence, hashes, procedural differences and limits are recorded in [the conversion design](fl-studio-db-conversion-design.md#live-db-trial-result-2026-11-18-125613-utc). Save/export remain manual; coordinator integration is pending.

## Open questions

- FL Studio's real SysEx size limit and its reply latency under load. The 1024-byte and 2.0 s pilot settings are not measured limits.
- The cause of the two setup-phase timeouts in the live inspect log.
- Whether two FL Studio instances or two scripts on one port could both answer a command. Strays are recorded, but not attributed.
- Whether the trial tolerances (0.001 normalized, 0.05 dB) match the fader's real resolution. Live, a write of 0.78 read back as 0.7800000002607703. The tolerances are the gain trial's settings, not measured limits.
- How FL Studio's normalized fader maps to dB. The live points (0.80 → 0 dB, 0.78 → −0.574523 dB, 0.76 → −1.152742 dB) do not define the curve. Image-Line documents no dB-to-normalized helper. A candidate curve and a pre-registered calibration are proposed in [fl-studio-db-conversion-design.md](fl-studio-db-conversion-design.md).


## HT01 calibration-only extension (2026-11-25; uncommitted review checkpoint)

The runtime/device v2 protocol now recognizes `ht01-mixer-cal-v1`, scoped to metadata title `HT01 Live Calibration v001`, insert 5 / Bass, API45 and Producer Edition26.1.7 build5654. This does not extend the accepted production conversion or enable HT01 dB/restore commands. Frozen research scripts are unchanged.

The [approved design](../superpowers/plans/2026-11-25-ht01-calibration-design.md) pins the 36-target sequence, primary/staging roles, operating range proposal, numerical criteria and digest. New HT01 probe commands carry `plan_digest`; existing command bytes are unchanged. Device session history enforces order and blocks further calibration writes after uncertainty. Normal restoration is limited to the captured initial target and requires the same session. Reload does not establish authority to recover an unknown write.

The non-frozen adapter-local runner exclusively creates a pending-first JSONL log, never retries, and stops unknown effects. Its evaluator requires the complete chain and exact restore before judging the hypothesis. The separate operator helper retains failed MIDI cleanup ownership. Tests and mutation evidence are simulated only. Installation, a live run, uncertain-outcome recovery procedure and production-profile adoption remain separately reviewed/authorized work.
