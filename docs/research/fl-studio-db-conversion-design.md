# FL Studio bridge — dB gain conversion design

**Status (2026-11-18):** the calibration (section 5) **ran live and accepted H** for normalized 0.62–0.86 on the tested build and insert (section 7). `set_gain_db` is **implemented and simulated, not installed or run live** (section 8), and the live dB trial is pre-registered (section 9). *Supersedes the earlier status "Design draft for review. Not implemented."* This is the next increment of the FL Studio command bridge ([contract](fl-studio-bridge-contract.md)), still technical feasibility work under the owner-authorized trial (ADR-009 unchanged, FR-14 withdrawn).

It specifies:
- how an intended dB cut becomes **one absolute normalized fader target**;
- how bounds are enforced;
- how FL Studio's dB read-back decides success;
- the calibration that has to accept a conversion before any of this is implemented.

The live fader is left as observed (insert 2 `Bass` at 0.76 / −1.152742 dB, unsaved).

## 1. What Image-Line documents (checked 2026-11-18)

**Image-Line MIDI scripting manual** (`midi_scripting.htm`, fetched 2026-11-18):
- `mixer.getTrackVolume(index, mode)` returns the normalized volume (0–1.0). It returns dB only with `mode = 1`, which is *read only* (marked for API 14).
- `mixer.setTrackVolume(index, volume, pickupMode = PIM_None)` takes **only a normalized value** "between 0 and 1.0"; `pickupMode` overrides FL's default pickup option.
- `channels.getChannelVolume(…, useDb)` and `channels.getEqGain(…, mode)` likewise read dB but do not convert.
- **No documented helper converts dB to a normalized fader value**, and the manual documents no `utils` module.

**Shipped but undocumented: `utils.VolTodB`** in `FL Studio 2026/Shared/Python/Lib/utils.py`. It computes `round(20·log10((11^v − 1)/10), 1)`.
- It is **not** the mixer-track curve. At the three live points it is off by **−4.72, −4.63 and −4.55 dB**; at the default fader 0.8 it gives −4.72 dB, where FL reads 0 dB.
- It also rounds to 0.1 dB. It is presumably meant for another control; that is not established.
- It is rejected for this purpose.

**Third-party variant.** Native Instruments' Komplete Kontrol script, installed in the user's FL Studio hardware folder, scales the input by 1.25 in the same expression. This gives the candidate curve

  **H(v) = 20·log10((11^(1.25·v) − 1) / 10)**,  inverse **H⁻¹(d) = ln(10·10^(d/20) + 1) / (1.25·ln 11)**.

- It is restated here as a mathematical hypothesis; no code is copied. It is a third-party script, not Image-Line documentation, so it is not evidence of FL Studio's internal curve.
- What makes it worth testing is that it reproduces all three live read-backs (below) to within about **2·10⁻⁶ dB**, and that H(0.8) = 0 dB exactly and H(1.0) = +5.59 dB.

**Live read-backs available (all on insert 2 `Bass`, API 45):**

| Normalized (read back) | dB (read back, mode 1) | H(v) − measured | Source |
| --- | --- | --- | --- |
| 0.800000 | 0.000000 | 0 (to 1e-15) | gain trial (6-decimal output) |
| 0.7800000002607703 | −0.5745229721069336 | −1.8·10⁻⁶ dB | live inspect and write logs |
| 0.7599999997764826 | −1.1527416706085205 | −1.0·10⁻⁶ dB | live write log |

**Why these points do not establish the curve:**
- These three points (two intervals, 0.80 → 0.78 → 0.76) are what **selected** H, so they cannot also test it.
- They span 0.04 normalized and only 1.15 dB.
- The trial also needs cuts of up to 3 dB from arbitrary starting faders.

H is therefore a **hypothesis to be tested by calibration**, not a conversion.

## 2. Conversion principle

FL Studio's own dB read-back (`getTrackVolume(i, 1)`) is the authority, both for the fader's dB value and for judging success.
- The conversion only **proposes** one absolute normalized target; FL's read-back decides whether it achieved the intended dB.
- This is the fader's nominal gain as FL reports it, not a measurement of rendered audio. Whether exported audio changes by the same amount is a separate check, made later on exports.

Options considered:

| Option | Description | Decision |
| --- | --- | --- |
| **A. Closed-form inverse of H** | target = H⁻¹(start dB + intended change) | **Preferred**, only if calibration accepts H over the operating range |
| B. Calibration table with monotone interpolation | piecewise-linear interpolation in (v, dB) between measured points; no assumed formula | Fallback if calibration rejects H. It must then pass its own held-out validation |
| C. Closed-loop search (write, read dB, correct, repeat) | needs no model | **Rejected** for the trial: it breaks "one absolute write per command", leaves intermediate fader states, and makes duplicate and uncertain-outcome handling ambiguous |

## 3. From an intended dB cut to one absolute target

**Inputs:**
- the starting state `before = {normalized v₀, db d₀}` from an `ok` inspect;
- the intended change Δ, in dB and negative (a cut).

**Steps** (pure functions in `bridge_protocol.py`, so the client and the script compute the same value):
1. **Start check.** The starting fader must lie inside the calibrated range [v_lo, v_hi], and the model must agree with FL at the start: |H(v₀) − d₀| ≤ ε_cal. Otherwise refuse `uncalibrated_start`.
2. **Target dB:** d* = d₀ + Δ, relative to the **read-back** dB, not to H(v₀).
3. **Target fader:** v* = H⁻¹(d*), in full float precision; no rounding is imposed (live, FL stored 0.76 as 0.7599999997764826).
4. **Bounds** (refusal code `bounds`, before anything is sent):
   - Δ ∈ **[−3.0, −0.1] dB**: at most the gain trial's `MAX_INTENDED_DB_CUT` of 3 dB, and at least twice the 0.05 dB read-back tolerance, so that success is distinguishable from no change;
   - v* > 0 and v* ≥ v_lo, so the target lies inside the calibrated range;
   - v₀ − v* > 0.002 normalized, twice the normalized read-back tolerance, so the fader move is observable.
   - The **normalized cut cap of 0.03 is replaced by the dB cap** for dB commands. Under H, a 1 dB cut from 0.8 needs a normalized cut of 0.0347 and 3 dB needs 0.1029, so the current cap would refuse cuts of about 0.87 dB or more at the default fader. Plain `set_gain` keeps its normalized bounds unchanged.

**Command** (a later increment; protocol version stays 1 with a new type): `set_gain_db` with
- the identity fields (ID, project title, insert index and name);
- `expected {normalized, db}`;
- `intended_db_change` (Δ);
- `target_normalized` (v*, computed by the client);
- `conversion` (an identifier of the accepted conversion and its calibration, e.g. `"H1.25-cal-2026-11-…"`).

The script **recomputes** v* from `expected` and Δ with the shared function and refuses `conversion_mismatch` if the results differ by more than 1e-9. That catches a client and script on different versions. It also refuses an unknown `conversion` identifier. All existing pre-write checks (API, identity, transport, `safeToEdit`, starting gain within 0.001 / 0.05 dB) and the duplicate and uncertain-outcome rules apply unchanged.

**Size:** the command grows by two fields; the worst-case size test is extended before implementation.

## 4. How dB read-back decides success

After the single write, the script reads normalized v₁ and dB d₁ back.

| Outcome | Rule | Status |
| --- | --- | --- |
| Fader moved as commanded **and** dB as intended | \|v₁ − v*\| ≤ 0.001 **and** \|d₁ − (d₀ + Δ)\| ≤ **0.05 dB** | `applied`, recording intended Δ, achieved d₁ − d₀ and the miss (d₁ − d₀) − Δ |
| Fader moved as commanded, dB missed | \|v₁ − v*\| ≤ 0.001 but \|d₁ − (d₀ + Δ)\| > 0.05 dB | `applied_off_target` (new): the write happened and the state is known, but the **conversion failed**. Not uncertain |
| Fader not where commanded, or read-back failed | as now | `uncertain` (`readback_disagrees`, `exception_after_write:…`) |

- **0.05 dB** is the existing trial tolerance (`DB_TOLERANCE`). For scale, under H the normalized tolerance of 0.001 corresponds to about 0.027–0.034 dB between faders 0.5 and 1.0.
- **Client behavior:**
  - `uncertain` keeps the existing write guard: inspect before the next write.
  - `applied_off_target` sets a separate **conversion block**: no further `set_gain_db` until the conversion is re-calibrated. Plain normalized `set_gain` stays available.
  - The client also checks `applied` replies for consistency: recomputed v*, and the miss within tolerance.

## 5. Calibration procedure (needed before implementation)

**Scope.** Owner-authorized, on the disposable `bass-bridge-test.flp` (metadata title `Thesis Bass Bridge Test`), insert 2 `Bass`, transport stopped.
- No audio is rendered or judged; only FL's own read-back is recorded.
- The project is not saved during calibration. At the end the fader is returned to its starting value, verified by inspect.
- **Starting value** *(decided by the owner on 2026-11-18, superseding the open choice that stood here)*: the exact normalized gain read by a **fresh inspect** at the start of the run. The restore targets that value exactly.

**Mechanism.** Calibration needs fader moves that a trial cut must not make: raises, and jumps larger than 0.03. It therefore uses a **separate calibration command**, `probe_gain`, never `set_gain`. It is implemented and simulated (2026-11-18), not yet installed or run; see "Pinned implementation" below.
- Each probe is one absolute write followed by a read-back of normalized and dB. It is subject to the same identity, transport and `safeToEdit` checks, the duplicate history and the uncertain-outcome rules.
- Probes are not bounded to cuts; their targets come only from the pre-registered list below.

**Pre-registered test points.** Fixed before running, and **independent** of the three points that selected H: none of 0.76, 0.78 or 0.80 is used.
- Primary range (trial operating range, start faders around default and cuts of up to 3 dB): **0.62, 0.65, 0.68, 0.71, 0.73, 0.75, 0.77, 0.79, 0.81, 0.83, 0.86**.
- Wider check (whether H holds beyond the trial range; not required for acceptance in the primary range): **0.30, 0.45, 0.55, 0.92, 1.00**.
- Order: a fixed shuffled order (seed recorded), so that monotone drift or hysteresis does not line up with the curve. The pinned order is given below.
- Each primary point is visited **twice**, once approached from above and once from below, to detect pickup or hysteresis effects.

**Recorded per probe:** the command and reply as in the bridge logs, including written target, read-back normalized, read-back dB, API and FL version.

**Acceptance (pre-registered):**
- Every probe's normalized read-back is within **1e-6** of its target, so the fader stores what is written up to float precision. A larger deviation means quantization that the conversion must model.
- For **every** primary-range probe: |H(v₁) − d₁| ≤ **ε_cal = 0.01 dB**, evaluated at the read-back v₁.
  - Why 0.01 dB: it is a fifth of the 0.05 dB success tolerance, so model error leaves most of the success budget to everything else.
  - It is far above the about 2·10⁻⁶ dB agreement already seen, so it does not tune the result to those points.
  - It is still small enough to reject a different curve shape.
- The two visits to each point agree to within 1e-6 dB, so there is no hysteresis.
- **If all hold:** H is accepted for [0.62, 0.86] only, under its conversion identifier. Wider-check results are reported, and they extend the accepted range only if they also pass.
- **If any primary probe fails:** H is rejected. Option B is used only if a table built from the even-indexed primary points predicts the odd-indexed ones within 0.01 dB (held-out validation). Otherwise dB conversion is **not established**, and dB commands stay unimplemented.

**What the calibration does not show:**
- whether the curve is the same on other inserts, the master track, other FL Studio versions, or with other pickup settings;
- whether rendered audio changes by the fader's dB.

These are open questions, not assumptions.

### Pinned implementation (2026-11-18, before any run)

The plan and the acceptance criteria are fixed in code. Any change alters the plan digest, which a test checks.

**Plan** (`bridge_protocol.CALIBRATION_*`):
- ID `fl-mixer-cal-v1`, on project title `Thesis Bass Bridge Test`, insert 2 `Bass`.
- Primary and wide points as listed above.
- Seed `20261118`. The order was drawn once with `random.Random(20261118).shuffle` over the primary points twice and the wide points once, repeated until three conditions held: the first position is a wide point (so no primary visit depends on the unknown start); no position repeats its predecessor; every primary point is approached once from above and once from below. That took 2,661,526 shuffles. The literal is authoritative:

  `0.55, 0.62, 0.73, 0.71, 0.81, 1.0, 0.86, 0.81, 0.86, 0.83, 0.77, 0.79, 0.68, 0.71, 0.45, 0.75, 0.83, 0.73, 0.92, 0.79, 0.75, 0.65, 0.3, 0.65, 0.68, 0.62, 0.77` (27 probes)
- Plan digest, which covers the plan and the three tolerances (`evaluate_calibration.PINNED_PLAN_DIGEST`): `8b4512b645cfb48ebe74be337e70d1663bc4a5df39195ee1d6fc175e834fbd00`.

**`probe_gain` command** (protocol v1):
- Fields: the identity fields, `plan`, `step`, `expected {normalized, db}` and `target_normalized`.
- **Validation before any FL Studio call** (refusal code `plan`):
  - the plan ID, project title and insert must be the plan's;
  - `step` must be a sequence index whose pinned target equals `target_normalized` exactly, or `"restore"` with a target in (0, 1].
- **Checks kept from `set_gain`:** API, project title, insert, stopped transport, `safeToEdit`, and the starting gain within 0.001 / 0.05 dB of `expected`.
  - Each probe expects the previous probe's read-back, so the chain also detects any change between probes.
  - No cut bounds apply to probes.
- **The write:** one `setTrackVolume`, then read-back. `applied` if the fader reads the target within 0.001; otherwise `uncertain`.
  - The post-write uncertain-outcome boundary, the duplicate replay and conflict rules, the pending journal and the client write guard all apply unchanged. An uncertain probe blocks every write, including `set_gain`.
- **Restore rule:**
  - Step 0 records the normalized gain read before it in the script's session history. A restore must then target exactly that value (`precondition_failed` / `restore_target`).
  - After a reload no start is recorded, and the client takes the start from its log.

**Runner** (`bridge_client.py calibrate --log … [--run-id …]`):
1. A fresh inspect of the plan's insert. The run is refused if it is not `ok`, or if the write guard is still set after it.
2. Probes `cal-<run>-00` … `cal-<run>-26` in order.
3. A restore probe `cal-<run>-restore-1` back to the inspect's exact normalized value.
4. A final inspect. `completed` requires every probe `applied`, and a final inspect equal to the start in both normalized and dB.

If a probe fails:
- **Known refusal** (nothing written): the runner stops and restores.
- **Unknown outcome** (`uncertain`, timeout, invalid reply): it stops **without** writing. `calibration-restore --run-id …` later inspects, which clears the guard if `ok`, and restores to the start recorded in the log.

The full request and reply log is the CLI's `--log`.

**Evaluation** (`evaluate_calibration.py --log … --run-id … --out …`) applies exactly the criteria above:
- `normalized_exact` (1e-6), `primary_within_tolerance` (|H − dB| ≤ 0.01 at every primary probe) and `visits_agree` (1e-6 dB).
- Verdict: `incomplete`, `H_accepted` (for [0.62, 0.86] only), or the held-out table fallback, giving `table_accepted` or `not_established`.
- Wide points, visit differences, every probe's residual, and the restore and final inspect are reported.
- **Record validation first** *(added after review on 2026-11-18; until then the evaluator trusted any applied record whose ID had the run's prefix)*. Before any number is used, every record of the run is checked. A run with any problem gets the verdict **`invalid_records`**, never an accepted curve. The checks:
  - each command passes the protocol's validation: the plan ID, the plan's project and insert, and the pinned target for its step. So an edited target or identity is refused even when the reply was edited to agree;
  - each probe's ID matches its step;
  - each reply is valid, answers its command, and is consistent with it: observed identity, start against `expected`, read-back at the target, and the `db_change` arithmetic;
  - the initial inspect is an `ok` reply for the plan's project and insert;
  - applied steps appear in log order 0, 1, 2 … with no gap;
  - each step expects exactly the previous step's read-back, and step 0 the initial inspect's gain;
  - any second reply to a step is a faithful replay;
  - every restore targets exactly the initial inspect's normalized gain.

  The plan, positions and numerical criteria are unchanged, and so is the digest.

**Simulated checks:** 52 new test cases (31 test functions) among 224 bridge test cases. One of them confirms that H reproduces the three selecting points (to 1e-5 dB). Simulated outcomes:
- a fader following H gives `H_accepted` with an exact restore;
- a smooth tilt off H (up to 0.024 dB) is rejected for H and accepted by the table (held-out error 0.0068 dB);
- a 0.02 dB bump at one primary point gives `not_established`;
- a 0.001 dB second-visit difference fails `visits_agree`;
- a stored value 2e-6 off its target fails `normalized_exact`.

**Note on the fallback.** With these fit points, the table's own interpolation error on a curve shaped like H is already up to about 0.007 dB. So it uses most of the 0.01 dB tolerance, and option B is a narrow fallback. *(Recorded here before any run; the criterion is unchanged.)*

## 6. Increments after review

1. The `probe_gain` command and calibration run, with simulated tests first. **Done 2026-11-18:** reviewed, installed and run live, with verdict `H_accepted` (section 7).
2. Only if calibration accepts a conversion: shared conversion functions, `set_gain_db` with the checks and statuses above, client conversion block, tests, then a live trial with one dB cut, its duplicate and a conflict. **Implemented and simulated 2026-11-18 (section 8); the live trial (section 9) awaits review.**
3. Save/export automation and coordinator integration stay separate.

## 7. Calibration result (live, 2026-11-18)

**Evidence** (both retained outside the repository, read and not modified):
- **Request/reply log:** `C:/uni/Bachelor-Thesis/test/fl-calibration/fl-calibration-live.jsonl`.
  - 39,925 bytes, 61 records (29 `pending`, 32 `replied`), SHA-256 `adbc5e58bccd4e943d221c5dd835b845f0fe90f25dca182a66f13e2b88b7601e`, last written 12:42:06 UTC.
  - Run ID `live20261118codex`. The run was executed after the script reload; the log does not record who ran it.
- **Retained evaluation:** `C:/Users/my_goconut/.codex/.chatgpt-projects/g-p-6ac5f45645e88191bc2efa98d1049408/fl-calibration-evaluation.json`, 10,113 bytes, SHA-256 `1f3e2ed2f9d2af8c6b99b644ba2398f102ae8948a4689ad47930cce6bd75b6e8`.

**Installed script during the run:** `device_bridge.py` `bda34ec4…`, `bridge_protocol.py` `e69eac73…`, byte-identical to the reviewed repository versions. The copies they replaced (`477139b1…` / `901c69af…`) are kept in `test/fl-calibration/installed-before-calibration/`.

**What else the log contains:**
- the fresh inspect at 12:39:38 UTC;
- a no-write version check at 12:40:02 UTC, which the old script answered `invalid_command` / `type`;
- the run's own initial inspect at 12:42:06 UTC;
- the final inspect.

**Revalidation (Claude Code, 2026-11-18).** The repository evaluator (`evaluate_calibration.py`, with record validation) was re-run on the log. Its result equals the retained evaluation **in every field**:
- **Verdict:** `H_accepted`. No record problems; plan digest `8b4512b6…` matches.
- **Completeness:** all 27 probes applied; every normalized read-back within **4.5·10⁻¹⁰** of its pinned target; both visits of every primary point read identical dB.
- **Curve error, max |H − read-back|:** **2.40·10⁻⁶ dB** at the primary points (0.83), and 1.77·10⁻⁶ dB at the wide points. All 5 wide points are within 0.01 dB, including 0.30 (−16.73 dB) and 1.00 (+5.59 dB).
- **Restore:** to **0.7599999997764826 / −1.1527416706085205 dB**, the exact gain of the run's initial inspect, confirmed by the final inspect. The write guard rebuilt from the log ends clear.

**What this accepts:** H for normalized **[0.62, 0.86]** on FL Studio `Producer Edition v26.1.7 [build 5654]`, API 45, project title `Thesis Bass Bridge Test`, insert 2 `Bass`, with the default pickup mode.

**What it does not accept:**
- other inserts, the master, other builds or other pickup settings;
- rendered audio levels.

The wide-point agreement is reported, but it does **not** extend the accepted range: the predeclared rule requires passing wide points only to extend it, and no extension was declared.

## 8. `set_gain_db` as implemented (2026-11-18; simulated, not installed or run live)

**Conversion** `fl-mixer-h125-v1` (`bridge_protocol.CONVERSION_*`), shared by client and script:
- the curve: `h_db`, its inverse `h_inverse`, and `db_target(start_db, change) = h_inverse(start_db + change)`;
- **range:** [0.62, 0.86], inclusive, compared at 1e-9;
- **scope:** project title, insert and FL Studio build of the calibration. *(An addition to sections 3–4: the calibration accepted H only there, so the command refuses anything else.)*
- the plan and run that accepted it.

A different curve or a new calibration gets a new ID; nothing else is accepted.

**Command `set_gain_db`:** the identity fields, `expected {normalized, db}` (from an `ok` inspect), `intended_db_change` Δ, `target_normalized` and `conversion`.

Validation before any FL Studio call is the same code in client and script. The refusal codes are:

| Check | Code |
| --- | --- |
| conversion ID | `conversion` |
| project title and insert outside the scope | `conversion_scope` |
| Δ outside [−3.0, −0.1] dB | `bounds` |
| starting fader outside the range | `range` |
| starting read-back more than 0.01 dB off H | `uncalibrated_start` |
| target differs from `db_target(expected.db, Δ)` by more than 1e-9 | `conversion_mismatch` |
| target outside the range | `range` |
| fader move ≤ 0.002 normalized | `bounds` |

*Refines section 3, step 1, which used `uncalibrated_start` for both start checks. The range check now has its own code, `range`.*

**Script, before writing** (each refusal writes nothing):
- every `set_gain` check: API, project title, insert, stopped transport, `safeToEdit`, and the starting gain within 0.001 / 0.05 dB of `expected`;
- the FL Studio build (`precondition_failed` / `uncalibrated_build`);
- the **actual** start inside the range (`range`) and on the curve (`uncalibrated_start`).

There are no normalized cut bounds: the 0.03 cap does not apply to dB commands (section 3).

**After the single write** (judged by both read-backs, as in section 4):

| Read-back | Status |
| --- | --- |
| normalized not within 0.001 of the target | `uncertain` / `readback_disagrees` |
| fader at the target, and \|(after.db − before.db) − Δ\| ≤ 0.05 dB | `applied` |
| fader at the target, dB missed | **`applied_off_target`** / `db_off_target` |

`applied_off_target` is a **known write, not uncertain**: the reply keeps the observed before, after and `db_change`. Failures after the setter remain `uncertain`.

**Kept unchanged:**
- duplicate replay, including of an `applied_off_target` result, and refusal of a reused ID with different content;
- the pending journal;
- the post-write uncertain boundary;
- the write guard.

**Client:**
- **Before sending:** it computes the target and checks the bounds and range, raising locally with nothing sent.
- **Checking replies:** it judges `applied` and `applied_off_target` against the command: start, target, the miss on the correct side of 0.05 dB, and the arithmetic. An `applied` reply whose dB missed is `invalid_reply`.
- **Write guard:** `uncertain` sets it.
- **Conversion block:** `applied_off_target` sets it (`conversion_block`, rebuilt from the log). It stops every further `set_gain_db` with that conversion; normalized `set_gain` stays available.
- **CLI:** `set-gain-db … --db-change −1.0 --log …`. Exit codes: 0 `applied`, 8 `applied_off_target`, 5 blocked (write guard or conversion), 6 refused locally. `calibration-restore --start-log` reads a run's start from the calibration log while appending to another log.

**Simulated checks:** 67 new test cases among 303 bridge test cases, written before the implementation. They cover:
- the conversion's pinned constants, and that H in the protocol equals the evaluated H;
- inverse consistency across the range (to 1e-12 normalized and 1e-9 dB);
- 1, 2 and 3 dB targets;
- accepted cuts at −0.1, −1, −2 and −3 dB, and refused ones at −0.09, −3.01, 0, +0.5 and −10;
- range boundaries: the start above, below or on the lower edge, a target below; both edges inclusive, including a 3 dB cut landing exactly on 0.62;
- an off-curve start;
- wrong conversion IDs and scope, and a target disagreeing by 1e-6 (one within 5·10⁻¹⁰ is accepted);
- reply validation and consistency for `applied_off_target`;
- applied 1, 2 and 3 dB cuts with one write each;
- off-target versus uncertain outcomes;
- interruptions: the setter or the read-back failing after the write, a `None` read-back, a timeout, a failed send;
- every pre-write refusal, including build and actual start;
- the script refusing a disagreeing target or an unknown conversion before writing;
- duplicate delivery, including an off-target replay, and conflicts;
- the conversion block in memory and rebuilt from the log; the uncertain path not setting it;
- CLI refusals.

The L1 target computed by FL Studio's own Python 3.12.1 equals the client's, `0.7257362319041692`.

## 9. Live dB trial plan (pre-registered 2026-11-18; subsequently run, see result below)

**Where:** `bass-bridge-test.flp`, insert 2 `Bass`, transport stopped, with the reviewed script installed and reloaded.
**Log:** a new log, `C:/uni/Bachelor-Thesis/test/fl-calibration/fl-db-trial-live.jsonl`.
**Starting point:** the state left by the calibration (0.7599999997764826 / −1.1527416706085205 dB). If the fresh inspect differs, its values are used and the targets are recomputed by the same function.

The two write targets are not calibration positions: they lie at least 0.0043 and 0.0086 normalized from every pinned probe.

| Step | Command | Expected (target) | Success criterion |
| --- | --- | --- | --- |
| L0 | `inspect` | start | `ok` |
| L1 | `set-gain-db`, ID `live_db_20261118_L1`, Δ = −1.0 dB | target **0.7257362319041692** (H⁻¹(−2.1527416706)) | `applied`; fader within 0.001 of target; achieved change within 0.05 dB of −1.0; miss recorded |
| L2 | L1 resent, same ID and payload | the recorded L1 result | `applied`, `replayed: true`, identical observations |
| L3 | same ID, Δ = −2.0 dB | — | `invalid_command` / `duplicate_id_conflict` |
| L4 | new ID `live_db_20261118_L4`, Δ = −2.0 dB from L1's read-back | ≈ **0.6585855514** (exact value from L1's read-back dB) | as L1, against −2.0 dB |
| L5 | Δ = −3.0 dB from L4's read-back | ≈ 0.5619, below 0.62 | refused **locally** (`range`, exit 6), nothing sent |
| L6 | restore probe to L0's exact normalized gain: `calibration-restore --run-id live20261118codex --start-log <calibration log> --log <trial log>` (attempt `restore-2`) | 0.7599999997764826 | `applied` |
| L7 | `inspect` | — | exactly L0's normalized and dB |

**What the trial does not change:**
- An `applied_off_target` at L1 or L4 is a recorded result (conversion blocked, no further dB writes), not a reason to adjust anything.
- An `uncertain` outcome stops the trial, followed by inspect and restore.
- Saving stays manual; nothing is saved.

## Live dB trial result (2026-11-18, 12:56:13 UTC)

This result supersedes the earlier “not run” status for the pre-registered L0–L7 trial. Evidence is the external request/reply log `C:/Users/my_goconut/.codex/.chatgpt-projects/g-p-6ac5f45645e88191bc2efa98d1049408/fl-bridge-live-db.jsonl` (SHA-256 `14f4a7958ca8b5a8560209778905f8b45edbfd1e7818921eee82310e6057d3b7`). It contains the client journal and FL Studio replies; no Script output capture or audio export was taken.

Scope: FL Studio Producer Edition v26.1.7 [build 5654], API 45, metadata title `Thesis Bass Bridge Test`, insert 2 `Bass`. The metadata title does not verify the project file path.

| Step | Observed result |
| --- | --- |
| L0 | `ok`: normalized 0.7599999997764826, −1.1527416706085205 dB |
| L1, requested −1 dB | `applied`: target 0.7257362319041692; read-back 0.7257500002160668 / −2.152336835861206 dB; change −0.9995951652526855 dB; miss +0.0004048347473145 dB |
| L2, same command | `applied`, `replayed=true`, same before/after and change |
| L3, same ID, different cut | `invalid_command` / `duplicate_id_conflict` |
| L4, requested −2 dB from L1 | `applied`: target 0.658598941413758; read-back 0.6586250001564622 / −4.151548862457275 dB; change −1.9992120265960693 dB; miss +0.0007879734039307 dB |
| L5, requested −3 dB | Local `range` refusal; journal records `no_dispatch=true` |
| L6 | Restore `cal-live20261118codex-restore-2` returned `applied` |
| L7 | `ok`, exactly equal to L0 in both gain readings |

Both cuts satisfy the pre-registered 0.001 normalized and 0.05 dB tolerances. All recorded exchanges have empty stray/malformed reply lists. The replay flag documents the bridge's cached-result claim; identical absolute read-back alone cannot prove the setter was called only once.

Installed reviewed script hashes: `device_bridge.py` SHA-256 `54fb39aa1178060d94860986d70c9a7d3cbd43c166c1abf3a0a695b6d839e28f`; `bridge_protocol.py` SHA-256 `0220b3e9c560d6d7f2a536a2344a947767c2b1ec44c10b6148029c20b2d432db`.

Procedural differences from the plan: the log was saved in the Codex project folder above rather than `test/fl-calibration/fl-db-trial-live.jsonl`; write IDs use `db_live_20261118_L1` and `db_live_20261118_L4` rather than the planned `live_db_…` prefix. The sequence, conversion, numerical criteria and restore target were unchanged. L4 was computed from L1's actual read-back as planned.

No save or export command was issued. The trial establishes dB command/read-back behavior in the calibrated scope. It does not establish exported audio gain, other builds or inserts, coordinator integration, or an autonomous mixing result. Next work is connecting the reviewed bridge to admitted agent actions and defining render/export verification and recovery; save/export remain manual. No requirement status changes.

## Open questions

- Is H FL Studio's actual mixer-fader law, or only a close approximation in the range tested? This is answerable only by calibration; the manual is silent.
- Is the read-back dB (mode 1) rounded or quantized internally? The live values look like float32 (−0.5745229721069336).
- Does `pickupMode` affect where an absolute write lands? The bridge uses the default (`PIM_None` per the manual).
- Does the curve hold on other inserts and the master, and on other FL Studio versions?
- Does an export's measured level change match the fader's dB change? This is a separate, later check.
