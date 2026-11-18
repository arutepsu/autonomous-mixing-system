# FL Studio gain adapter feasibility trial

**Technical feasibility test**, owner-authorized on 2026-11-17 under ADR-009 (FL Studio integration deferred and optional). The trial runs on a disposable project or a dedicated project copy. FR-14 stays withdrawn, the `.flp` exclusion in `requirements.md` and the accepted ADR-009 are unchanged, and no `.flp` file is parsed or written by repository code. FL Studio's own scripting interface makes the mixer change; FL Studio's own Save and Export commands, run by hand, do the rest. The producer's kick/bass project and any musical evaluation come later. A proposed ADR and requirement for adopting FL Studio as the thesis renderer are drafted only after this trial establishes what works.

Codex drafted the script, its checks and this procedure (handoff 2026-11-17, 13:40); Claude Code took ownership on the same day.

## Status on 2026-11-17

*Superseded in part by the live result of 2026-11-18 (below): the script was hosted live, and its identity check, single write, read-back, duplicate refusal and reopen were observed.*

| Item | Status |
| --- | --- |
| FL Studio | 2026, build `26.1.7.5654` (`FL64.exe` file version) |
| Producer kick/bass project | Not found: no `.flp` in the user profile (searched to depth 5) or the thesis tree. A disposable project with a named test insert is used instead. |
| MIDI input to host the script | **None usable**, details below. Live steps are pending. |
| Local checks | Device script: 8 checks with simulated FL modules that expose only documented API names. Export check: 10 tests on synthetic WAV files. |
| Live insert identity, gain read-back, persistence, exports | Pending the MIDI input |

### MIDI endpoint finding

The script runs only as the controller script of an enabled MIDI input in FL Studio (Options > MIDI settings).
- **Classic MIDI API:** WinMM reports 0 MIDI input devices (1 output).
- **FL Studio's own device records** (registry `HKCU\Software\Image-Line\FL Studio 26\Devices`): they list MIDI *outputs* only (Microsoft GS Wavetable Synth, Microsoft MIDI Mapper) and no MIDI input. That held after FL Studio's settings were last written at 13:14, and again after FL Studio was started at 14:03 to let it enumerate devices. That session was still open when this was recorded, so whether it rewrites the records on exit is not established.
- **Windows MIDI Services:** the service (`midisrv`) runs and exposes only its diagnostic endpoints, "Service Test Loopback A/B" (`MIDIU_DIAG_LOOPBACK_A/B`). No app loopback pair exists under "MIDI 2.0 Loop Devices", and the Windows MIDI Services tools are not installed.

The diagnostic loopbacks are service test endpoints, not app endpoints; FL Studio did not record them, and they are not WinMM inputs. FL Studio therefore currently has no MIDI input that can host the script.

The one observation still to make by eye is the Input list in FL Studio's MIDI settings window; this check read FL Studio's records rather than its screen.

### Setup step for the owner

**Make one MIDI input available to FL Studio.** The simplest way needs no software installation: connect any USB MIDI controller. Then:
1. In FL Studio, open Options > MIDI settings and confirm the device appears under Input.
2. Select the device, click **Enable**, and set its **Controller type** to **Thesis Gain Trial (user)**. The script must already be installed (step 3 below).

External Python-to-FL messaging and virtual MIDI installation (an app loopback pair created with Windows MIDI Services tools, or loopMIDI) remain deferred, as instructed.

## Status update, 2026-11-17 (after decision pilot 1)

The owner asked to continue the bridge trial with one bounded Bass fader change on a disposable copy of the baseline. The live step has **not** been run, because FL Studio still has no MIDI input that can host the script.

### Endpoint re-check (evening)

- **Classic Windows MIDI (WinMM):** 0 inputs, 1 output (unchanged).
- **FL Studio's own device records** (`HKCU\Software\Image-Line\FL Studio 26\Devices`): MIDI outputs only (Microsoft GS Wavetable Synth, Microsoft MIDI Mapper), no input.
- **Windows MIDI Services:** the service runs, but its only endpoints are the diagnostic "Service Test Loopback A/B" (`MIDIU_DIAG_LOOPBACK_A/B`). The app and loop transports exist with no app-to-app loopback created, and the Windows MIDI Services tools are not installed.
- **Other drivers:** no loopMIDI, LoopBe or rtpMIDI.
- **The trial script** is not yet installed under `Settings/Hardware`.
- **FL Studio** was open with the owner's `trap-agent-A5.flp` during the check. It was left untouched.

**Conclusion:** no virtual MIDI input usable by FL Studio exists. Software must be installed, or a hardware controller connected, before the live step. Nothing was installed.

### Exact setup step for the owner (before any live attempt)

**Recommended: loopMIDI** (free, by Tobias Erichsen). It creates a standard Windows MIDI port that FL Studio lists as an input. The trial only needs the port to exist and be enabled: the script runs on **Reload**, and no MIDI messages have to be sent.
1. Download loopMIDI from the author's page, https://www.tobias-erichsen.de/software/loopmidi.html, and install it.
2. Start loopMIDI, type the port name `Thesis Bridge`, and click **+**. Leave loopMIDI running (or set it to start with Windows): the port exists only while it runs.
3. Copy `scripts/fl_studio/device_gain_trial.py` to `Documents/Image-Line/FL Studio/Settings/Hardware/Thesis Gain Trial/device_gain_trial.py`.
4. Restart FL Studio and open Options > MIDI settings. Confirm that `Thesis Bridge` is listed under **Input**, select it, click **Enable**, and set **Controller type** to **Thesis Gain Trial (user)**.

**Alternative without installing software:** connect any USB MIDI controller and use it in steps 3–4 instead of the loopMIDI port.

**Not recommended for this trial:** Windows MIDI Services app-to-app loopbacks. They need Microsoft's Windows MIDI Services tools, and whether FL Studio 2026 sees those endpoints is unverified.

### Bass test procedure, once the input exists (owner-driven; save and export stay manual)

1. **Copy.** Open `trap-baseline.flp`, use **Save As** to make `bass-bridge-test.flp` in the trial folder, and record its SHA-256. Work only in this disposable copy; leave `trap-agent-A5.flp` and the baseline untouched.
2. **Identity and starting gain.** In the installed script, set:
   - `PROJECT_TITLE` = the copy's Project Info (F11) title, for example `"Thesis Bass Bridge Test"` (the window title does not show the project name);
   - `INSERT_INDEX` and `INSERT_NAME = "Bass"`, as shown in the mixer;
   - `MODE = "inspect"`.

   Reload. Record the `RESULT` lines: `fl_studio`, `scripting_api`, `window_title`, `project_title`, `project_identity_check`, `insert`, `insert_name`, `current_normalized` and `current_db`. At the default fader position, `current_db` should read about 0 dB (the `DB_MODE` check).
3. **One bounded change.** Set:
   - `REFERENCE = (current_normalized, current_db)`;
   - `INTENDED_DB_CHANGE`, for example −2.0;
   - `TARGET_NORMALIZED`: the baseline minus a cut greater than 0.002 and at most 0.03;
   - `MODE = "apply"`.

   Reload **once**. Record `intended_db_change`, `sent_normalized`, `candidate_normalized`, `candidate_db`, `measured_db_change` and `label`. **APPLIED** requires the read-back to be within 0.001 normalized of the value sent and more than 0.05 dB below the baseline.
4. **Duplicate-command check.** With `MODE = "apply"` unchanged, reload a second time. The script must stop with "Current fader value is outside the reference tolerances." and write nothing. Then set `MODE = "inspect"` and reload: `current_db` must still equal `candidate_db`. Record both outputs.
5. **Leave the script in inspect mode.** Saving, reopening (verify mode) and exporting stay manual, as before.

### Duplicate-command handling (checked locally)

- **The command is absolute.** `TARGET_NORMALIZED` is an absolute fader value, not a relative step, so resending it cannot compound a second cut.
- **The baseline reference gates every write.** The script writes only while the fader still matches `REFERENCE` (within 0.001 normalized and 0.05 dB). The cut must exceed twice that tolerance, so after one apply the reference cannot match again: a resent command is refused before any write.
- **Tests:** `test_apply_records_intent_separately_from_measurement_and_prevents_second_write` sends the same apply twice and asserts exactly one `setTrackVolume` call. The local checks pass (`uv run python scripts/fl_studio/test_gain_trial.py`: OK).
- **Residual risk:** FL Studio also runs `OnInit` when the script loads at startup. If `MODE = "apply"` is left set and a *fresh* copy at the baseline gain is open, it would apply again. That would be a new first apply on that copy, not a second cut on the same one, but the procedure resets to inspect immediately after the apply for this reason.

### What worked live / what remains (as of 2026-11-17; see the live result of 2026-11-18)

- **Live so far:** FL Studio 2026 build 26.1.7.5654 starts and records its devices. The manual edits, saves and exports of the kick-cut trial and of pilot rounds A6 and A5 were done by the owner in FL Studio.
- **Not yet live (needs the MIDI input):**
  - the script loading as a controller script, and the insert identity read;
  - the `DB_MODE` check and the starting gain;
  - the single bounded write with dB read-back, and the duplicate-command refusal;
  - reopen verification through the script.

## Live result, 2026-11-18 (owner-pasted FL Studio output)

**Source.** The observations below are **attributed to the FL Studio Script output that the owner pasted** into the development chat. They were not captured by repository tooling.
- **Environment:** FL Studio Producer Edition 26.1.7, build 5654, MIDI scripting API **45**.
- **Script:** the installed copy at `Documents/Image-Line/FL Studio/Settings/Hardware/Thesis Gain Trial/device_gain_trial.py`, SHA-256 `93879cf1e5459195db7664bf46588bdc0fdafc4b8f3052798e1110de234860ee` (read 2026-11-18; left unchanged, `MODE = "inspect"`). It differs from the repository version only in its trial configuration, a hard-coded project title, documentation and an API-version guard (see "Installed script").
- **MIDI input:** this record does not say which input hosted the script.

| Step | Observed (owner-pasted output) |
| --- | --- |
| Project identity | `general.getProjectTitle()` returned the metadata title **`Thesis Bass Bridge Test`**, which matched. `ui.getProgTitle()` returned only **`FL Studio 2026`**. The script checked the project's **metadata title; it did not establish the file path**. |
| Insert identity | Insert **2** named exactly **`Bass`** (exact-name check passed) |
| Starting gain | normalized **0.800000**, read back as **0.000000 dB** (confirms dB mode 1 at the default fader position) |
| One bounded write | one absolute write to **0.780000**, read back as **−0.574523 dB** |
| Intended vs measured | intended −1.0 dB, measured **−0.574523 dB**, a difference of **+0.425477 dB**: the intended dB change was **not** achieved. The normalized cut of 0.02 is a bounded fader change, not a −1 dB change. |
| Duplicate command | reloading the same apply command was **refused by the baseline check before the write**; no second cut |
| Persistence | the owner saved the trial project manually, reopened it, and inspect read **0.780000 / −0.574523 dB** |

**What worked live:**
- the script hosted by FL Studio's controller-script mechanism;
- the metadata-title and exact insert-name checks, and the starting gain in normalized and dB form;
- one absolute write with dB read-back, refusal of a resent command, and persistence across a manual save and reopen.

**What remains:**
- **External command bridge** (sending a request from outside FL Studio): its own design.
- **Normalized-to-dB conversion:** one measured point (0.80 → 0.78 gives −0.574523 dB) does not define the fader curve, so an intended dB change cannot yet be turned into a fader value. This is its own design.
- **File-path identity:** no documented call establishes the project file path.
- **Export:** not part of this step. Save and export stay manual.

## Installed script and repository reconciliation (2026-11-18)

**Installed copy against the repository:**
- **Configuration:** `PROJECT_FILENAME = "bass-bridge-test.flp"` (unused in its identity check), `INSERT_INDEX = 2`, `INSERT_NAME = "Bass"`, `REFERENCE = (0.8, 0.0)`, `INTENDED_DB_CHANGE = -1.0`, `TARGET_NORMALIZED = 0.78`, `MODE = "inspect"`.
- **Identity check:** it calls `general.getProjectTitle()` when the API is ≥ 38 and compares the result with the hard-coded `"Thesis Bass Bridge Test"`. It records that the file path is not verified, and still records `ui.getProgTitle()`.

**Repository script (`scripts/fl_studio/device_gain_trial.py`), reconciled with the live findings:**
- The identity check is now the metadata title, `general.getProjectTitle()` against a configurable `PROJECT_TITLE` (set in Project Info, F11), with the result recorded as "metadata title matches; file path not verified by this API".
- The window title (`ui.getProgTitle()`) is recorded, not checked.
- `MIN_API_VERSION` is 38.
- `DB_MODE = 1` is now documented.
- The repository keeps blank defaults; the installed copy keeps the owner's trial configuration.

**Local checks** (`scripts/fl_studio/test_gain_trial.py`, 9) replay the live values: insert 2 "Bass", 0.800000 / 0.000000 → 0.780000 / −0.574523 dB with an intended −1.0 dB, the duplicate refusal before any write, and verify after reopen.

## API calls (Image-Line MIDI scripting manual; corrected 2026-11-18)

The script uses only these documented calls. The local check fails if it calls anything else.

| Call | API | Use |
| --- | --- | --- |
| `general.getVersion()` | 1 | Scripting API version |
| `general.safeToEdit()` | 29 | Edit guard |
| `general.getProjectTitle()` | 38 | Project metadata title (Project Info); the identity check (so the trial needs API ≥ 38) |
| `ui.getVersion(5)` | 1 | FL Studio version string |
| `ui.getProgTitle()` | 1 | Program title; recorded only (live: `FL Studio 2026`, no project name) |
| `mixer.trackCount()` | 1 | Number of tracks, including Master (0) and the last "current" track; inserts are 1 … `trackCount() − 2` |
| `mixer.getTrackCount()` | 38 | Documented as well (excludes Master and "Current"); not used by the script |
| `mixer.getTrackName(i)` | 1 | Insert identity |
| `mixer.getTrackVolume(i, mode)` | 1 | Normalized fader value (0.0–1.0, default 0.8), or dB with `mode = 1` (documented) |
| `mixer.setTrackVolume(i, v)` | 1 | The only write: normalized fader value |
| `transport.isPlaying()` | 1 | Refuse while playing |

**Correction (2026-11-18), superseding the 2026-11-17 statement here:** the earlier claim that `general.getProjectTitle()` and `mixer.getTrackCount()` are undocumented was **wrong**. It was based only on the IL-Group stub repository, which lacks them. Image-Line's MIDI scripting manual documents both from API 38. Live on API 45, `general.getProjectTitle()` returned the project metadata title, while `ui.getProgTitle()` returned only `FL Studio 2026`, so the window-title check that replaced Codex's call could not have identified the project. The script again uses `general.getProjectTitle()`, and the minimum API is 38.

**Gain mapping:**
- `mode = 1` returns dB (documented; live 0.800000 read 0.000000 dB). The normalized-to-dB curve is not documented, and the script never converts dB to a fader value itself.
- The intended change (`INTENDED_DB_CHANGE`, dB) is recorded separately from the normalized value actually sent (`TARGET_NORMALIZED`) and from FL Studio's dB read-back.
- Live, 0.80 → 0.78 gave −0.574523 dB against an intended −1.0 dB. Until the mapping is designed and checked at more than one point, the result is a **bounded normalized-fader change with a measured dB change**.

## Prepare the copy and baseline

1. **Project.**
   - With the producer's project: hash the original `.flp` with `Get-FileHash -Algorithm SHA256 -LiteralPath 'path-to-original.flp'`, then use **Save As** to make a dedicated copy in a trial directory outside the repository.
   - Otherwise: make a disposable project with two named inserts (for example "Trial Kick" and "Trial Bass") and audio routed to them, and save it as `gain-trial-copy.flp`.

   Give the copy a unique Project Info (F11) title, which the script checks (it cannot see the file name), and confirm that all samples and plugins load. Keep the source project and media immutable.
2. **Insert.** In the mixer, identify the numbered kick and bass inserts and their routing to Master. Choose one active insert whose fader has no automation, linked controller or initialized value that could override the test. Record its index and exact name. Keep playback and recording stopped during script operations.
3. **Script.**
   - Copy `scripts/fl_studio/device_gain_trial.py` to `Documents/Image-Line/FL Studio/Settings/Hardware/Thesis Gain Trial/device_gain_trial.py` (the active FL Studio user data folder). Edit that installed copy.
   - Set `PROJECT_TITLE` (the copy's metadata title), `INSERT_INDEX` and `INSERT_NAME`, and leave `MODE = "inspect"`.
   - Make the MIDI input available as described in **Setup step for the owner**, and select **Thesis Gain Trial (user)** for it.
   - Open **View > Script output**, select the script tab and use **Reload**. Copy every `RESULT` line into the trial record.
4. **Inspect.** Record `fl_studio`, `scripting_api`, `window_title`, `project_title`, `insert`, `insert_name`, `current_normalized` and `current_db`. If the insert is at its default 0.8, `current_db` should read about 0 (confirmed live on API 45). Export the baseline according to the common render procedure below.

## Apply once and check persistence

1. **Configure.** In the installed script:
   - set `REFERENCE` to the baseline pair `(current_normalized, current_db)` from inspect;
   - set `INTENDED_DB_CHANGE` to the intended cut (for example −0.5; at most 3 dB);
   - set `TARGET_NORMALIZED` to the fader value to send: the baseline minus a cut greater than 0.002 and at most 0.03 (trial bounds on the documented fader scale), staying above 0;
   - set `MODE = "apply"`.

   Reload once with the copy open. There is one setter call.
2. **Record the result.** Copy `intended_db_change`, `sent_normalized`, `candidate_normalized`, `candidate_db`, `measured_db_change`, `intended_minus_measured_db` and `label`.
   - **APPLIED** requires the read-back to be within 0.001 normalized of the value sent and more than 0.05 dB below the baseline.
   - The `label` says whether the intended dB change was reached within 0.05 dB at this one point, or whether the result is a bounded normalized-fader change with a different measured dB change. Report the measured dB change either way.
   - If the read-back fails, record the failure, return to inspect mode and recover from the saved baseline copy before a fresh trial.
3. **Persistence.**
   - Set the script back to `MODE = "inspect"` and reload. Save the candidate manually with Ctrl+S, hash the saved copy, close the project and reopen that same file.
   - Set `MODE = "verify"` and `REFERENCE` to the candidate pair `(candidate_normalized, candidate_db)`, then reload. Record **VERIFIED** only if the reopened gain is within 0.001 normalized and 0.05 dB of the candidate.
   - Record any license or plugin limitation affecting reopen. Disable the trial script or return it to inspect mode.
   - Render the candidate after the persistence check. If reopen is unavailable, export from the verified in-memory candidate if possible and record the state used.

## Common render procedure

- **Selection.** Export from the same Song arrangement and explicit Playlist time selection. Record the tempo or tempo map, the selection start and end in musical coordinates, the expected duration and FL Studio's displayed export duration. Use separate empty directories for baseline and candidate.
- **Settings.**
  - Use File > Export > Wave file: 44,100 Hz audio sample rate, 16Bit int, Split mixer tracks, and Include Master (where available).
  - Use Cut remainder, enable Trim PDC silence, and turn dithering off.
  - Keep quality, insert-effect and master-effect options identical across conditions.

  Record the actual sample rate, all render options and any deviations. If the split export omits Master, run a separate normal master render in both conditions with the same settings and selection except the split option. Document the routing and master effects; split insert stems bypass master effects. Keep the native WAV exports.
- **Check the exports** from the repository root, with the file names FL Studio gave each role (the same names in both directories):

  ```powershell
  uv run python scripts/fl_studio/check_exports.py --baseline 'path-to-baseline-dir' --candidate 'path-to-candidate-dir' --role kick=Kick.wav --role bass=Bass.wav --role master=Master.wav --changed bass --out 'path-to-trial-dir/export-check.json'
  ```

  The report records, per file, bytes, SHA-256, container, subtype, sample rate, channels and frames. It records whether each file meets the dist10 masking profile v1 (WAV, mono, 44,100 Hz, PCM_16; contract §2.3), with each mismatch named, and whether all six files share one sample rate and one frame count.
- **Timeline alignment** is established in three ways, all recorded in the report:
  1. the same Playlist selection and render settings in both conditions;
  2. equal frame counts across all six files;
  3. per role, a decoded comparison of baseline and candidate:
     - `bit_identical`, expected for the roles the change does not touch;
     - `lag_samples`, the cross-correlation peak of the channel means within ±4,410 samples, where 0 means no offset at sample resolution;
     - for the changed role, `gain_db`, the least-squares gain at lag 0, and `residual_ratio`.

  Matching frame counts alone only establish duration, so record the lag and identity results too. Do not trim individual stems by their first onset. Record any effects or plugin state that make an offset uncertain.
- **Format.**
  - The dist10 WAV boundary admits only mono 44,100 Hz PCM_16. FL Studio's split export is often stereo: keep those originals unchanged and record the mismatch for the next design step.
  - If mono analysis copies are needed later, use one explicit channel rule for both conditions (for example the mean of L/R before quantization to signed 16-bit PCM). Preserve the timeline and frame count, record the tool, transform and hashes of derivatives and sources, re-run the export check, and document any stereo cancellation.
- **Close out.** Rehash the original producer project and compare it with the first hash. Keep project files and audio outside version control.

## Local checks

From the repository root:

```powershell
uv run python scripts/fl_studio/test_gain_trial.py
```

```powershell
uv run pytest -q tests/unit/test_fl_export_check.py
```

References: [Image-Line MIDI scripting manual](https://www.image-line.com/fl-studio-learning-content/fl-studio-online-manual/html/midi_scripting.htm), [FL Studio API stubs (Image-Line)](https://il-group.github.io/FL-Studio-API-Stubs/), [Image-Line export manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/fformats_save_export.htm).
