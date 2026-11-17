# FL Studio gain adapter feasibility trial

**Technical feasibility test**, owner-authorized on 2026-11-17 under ADR-009 (FL Studio integration deferred and optional). The trial runs on a disposable project or a dedicated project copy. FR-14 stays withdrawn, the `.flp` exclusion in `requirements.md` and the accepted ADR-009 are unchanged, and no `.flp` file is parsed or written by repository code. FL Studio's own scripting interface makes the mixer change; FL Studio's own Save and Export commands, run by hand, do the rest. The producer's kick/bass project and any musical evaluation come later. A proposed ADR and requirement for adopting FL Studio as the thesis renderer are drafted only after this trial establishes what works.

Codex drafted the script, its checks and this procedure (handoff 2026-11-17, 13:40); Claude Code took ownership on the same day.

## Status on 2026-11-17

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

## API calls (checked against the Image-Line FL Studio API stubs)

The script uses only these documented calls. The local check fails if it calls anything else.

| Call | API | Use |
| --- | --- | --- |
| `general.getVersion()` | 1 | Scripting API version |
| `general.safeToEdit()` | 29 | Edit guard (so the trial needs API ≥ 29) |
| `ui.getVersion(5)` | 1 | FL Studio version string |
| `ui.getProgTitle()` | 1 | Window title; must contain the trial copy's file name |
| `mixer.trackCount()` | 1 | Number of tracks, including Master (0) and the last "current" track; inserts are 1 … `trackCount() − 2` |
| `mixer.getTrackName(i)` | 1 | Insert identity |
| `mixer.getTrackVolume(i, mode)` | 1 | Normalized fader value (0.0–1.0, default 0.8), or "in decibels" by the `mode` flag |
| `mixer.setTrackVolume(i, v)` | 1 | The only write: normalized fader value |
| `transport.isPlaying()` | 1 | Refuse while playing |

**Corrections to the draft:** the draft called `general.getProjectTitle()` and `mixer.getTrackCount()`. Neither exists in the documented API, so the script would have stopped with an `AttributeError` in FL Studio. They are replaced by `ui.getProgTitle()` (the copy's file name, `PROJECT_FILENAME`, must appear in the window title) and `mixer.trackCount()` (Master and the current track are excluded). The minimum API version is 29 (`safeToEdit`), not 38.

**Gain mapping:**
- The stubs do not state which `mode` value returns dB, nor the normalized-to-dB curve. The script uses `mode = 1` (`DB_MODE`) and never converts dB to a fader value itself.
- The intended change (`INTENDED_DB_CHANGE`, dB) is recorded separately from the normalized value actually sent (`TARGET_NORMALIZED`) and from FL Studio's dB read-back.
- Until inspect confirms on this build that `mode = 1` reads about 0 dB at the default 0.8, and the mapping is checked at more than one point, the result is a **bounded normalized-fader change with a measured dB change**. One applied point does not establish the mapping.

## Prepare the copy and baseline

1. **Project.**
   - With the producer's project: hash the original `.flp` with `Get-FileHash -Algorithm SHA256 -LiteralPath 'path-to-original.flp'`, then use **Save As** to make a dedicated copy in a trial directory outside the repository.
   - Otherwise: make a disposable project with two named inserts (for example "Trial Kick" and "Trial Bass") and audio routed to them, and save it as `gain-trial-copy.flp`.

   Check the file name in the window title before editing, and confirm that all samples and plugins load. Keep the source project and media immutable.
2. **Insert.** In the mixer, identify the numbered kick and bass inserts and their routing to Master. Choose one active insert whose fader has no automation, linked controller or initialized value that could override the test. Record its index and exact name. Keep playback and recording stopped during script operations.
3. **Script.**
   - Copy `scripts/fl_studio/device_gain_trial.py` to `Documents/Image-Line/FL Studio/Settings/Hardware/Thesis Gain Trial/device_gain_trial.py` (the active FL Studio user data folder). Edit that installed copy.
   - Set `PROJECT_FILENAME`, `INSERT_INDEX` and `INSERT_NAME`, and leave `MODE = "inspect"`.
   - Make the MIDI input available as described in **Setup step for the owner**, and select **Thesis Gain Trial (user)** for it.
   - Open **View > Script output**, select the script tab and use **Reload**. Copy every `RESULT` line into the trial record.
4. **Inspect.** Record `fl_studio`, `scripting_api`, `window_title`, `insert`, `insert_name`, `current_normalized` and `current_db`. If the insert is at its default 0.8, `current_db` should read about 0; if it does not, `DB_MODE` is wrong for this build, and the trial reports normalized values only. Export the baseline according to the common render procedure below.

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
