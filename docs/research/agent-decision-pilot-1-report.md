# Agent decision pilot 1: two-round report (FL Studio kick/808 development mix)

**Status:** 2026-11-17. The pilot ran under the owner's ADR-009 authorization for FL Studio feasibility work. The AI decision component proposed, and the owner executed each proposal manually in FL Studio on a fresh copy of the baseline. A deterministic script verified the result against rules fixed before the first proposal.

**Result.** Round 1 (A6) was rolled back on the master RMS guard. Round 2 (A5) passed every measured criterion. Its acceptance is qualified, because the execution-tolerance rule cannot be checked: the displayed fader readings were not recorded.

**Not claimed.** No listening judgment, perceptual masking claim or generalization is made.

The packet, menu, rules and owner decisions are documented in [`agent-decision-pilot-packet.md`](agent-decision-pilot-packet.md). The earlier manual kick-cut trial stays a **separate record**, [`fl-studio-kick-808-trial.md`](fl-studio-kick-808-trial.md). Neither it nor its candidate exports were an input to either round.

## 1. Sequence

1. **Baseline packet, built from the baseline exports only.**
   - `decision-packet.json`, SHA-256 `285426ecf0169fd739e5b4a36f79cc3da42e569658a3ec31825785f5c02276ae`: evidence summary, no frozen cells.
   - `action-menu.json`, `da7594e13e36748756fd204c259e5a51ba4ea13bd3378a2a7df9bde811c374e0`: A0 abstain; A1–A3 Kick −1/−2/−3 dB; A4–A6 Bass (808) −1/−2/−3 dB.
   - Verifier-only `verification-sets.json`, `9560eb7f68d67fba14e4e489690ee13024b9f99b0ca2edec81fc6eea9acc069e`: frozen cells, never given to the agent.
2. **Producer goal,** verbatim: **"make kick more dominant then bass"**. Recorded in `producer-goal.json` (`bebead07857dae561788173509a78e977da70c3837bf32482ab9db589280ee82`).
   - Verification mapping: protected stem P = kick, covering stem C = 808.
   - The other-stem guard was set by the owner before round 1: `candidate 808 RMS − baseline 808 RMS ≥ −3.25 dB`. Kick → 808 changes are reported descriptively.
3. **Round 1: the agent proposed A6** (Bass −3 dB).
   - **Inputs:** the bundle in `test/agent-pilot-input/` (the three files above) and `test/agent-pilot-run/agent-request.txt` (`47b5a2e1b1ae0e53ba3557291fa1b6e5f3817bd0a6e911b3d19b1168549723bc`). The owner ran it in a separate session; isolation as reported by the owner.
   - **Execution:** applied manually to `trap-agent-a6.flp` (`4fc42a08…`) and exported.
   - **Verdict:** rolled back on the master RMS guard. The baseline remained the accepted state.
4. **Round 2: the agent proposed A5** (Bass −2 dB), after the round-1 verification feedback (§4).
   - **Execution:** applied manually to a fresh copy of the baseline, `trap-agent-A5.flp` (`7bdf7a13…`), and exported.
   - **Verdict:** all measured criteria passed.

## 2. Verification, recomputed from the original exports

Each round is checked with `scripts/fl_studio/verify_pilot_round.py`. It reuses the trial analysis code (`analyze_trial.py`), the export check (`check_exports.py`), the dist10 front end and the masking kernel.
- **Rules:** the frozen rules of the packet document, for P = kick and C = 808.
- **Cells:** the frozen 206 cells of 808 → kick, and the 5,600 kick → 808 guard cells, reported descriptively.
- **Settings:**
  - mono analysis input `round_half_even((L + R) / 2)` on int16 counts;
  - the dist10 front end with history from call 0;
  - `T` = masker `thm` and `E` = target `en`;
  - stereo pooled-channel RMS and sample peak for the 808 and master guards.

| Check (rule) | Round 1: A6, Bass −3 dB | Round 2: A5, Bass −2 dB |
| --- | --- | --- |
| Integrity (exact): equal frames and rate, lag 0, kick decoded bit-identical | pass | pass |
| **Gate:** baseline exports reproduce the frozen 808 → kick margins exactly; a mismatch makes the round *not verified* | pass (max deviation 0 dB) | pass |
| Guard-set baseline agreement (reported; descriptive set, does not gate) | exact | exact |
| Measured 808 change (pooled RMS; least-squares gain) | −2.894 dB; −2.894 dB | −1.885 dB; −1.885 dB |
| Execution tolerance: displayed fader change within ±0.25 dB of the intended change | **unknown** (readings not recorded) | **unknown** (readings not recorded) |
| **Primary:** median Δ over the 206 cells ≤ −1.0 dB, and fewer cells > 0 dB | −2.894 dB; 206 → 199: **pass** | −1.885 dB; 206 → 201: **pass** |
| **808 level guard:** RMS change ≥ −3.25 dB | −2.894 dB: pass | −1.885 dB: pass |
| **Master peak guard:** ≤ −1.0 dBFS | −2.624 dBFS: pass | −2.150 dBFS: pass |
| **Master RMS guard:** candidate − baseline ≥ −1.5 dB | **−1.900 dB: fail** | −1.288 dB: pass |
| Kick → 808, descriptive: median Δ; newly exceeding among 5,483 at-risk cells | +2.894 dB; 28 (0.51 %) | +1.885 dB; 19 (0.35 %) |
| **Outcome** | **roll back** (baseline stays accepted) | **accept on measured criteria**; execution tolerance unknown |

**Levels:**

| | Kick peak / RMS | 808 peak / RMS | Master peak / RMS |
| --- | --- | --- | --- |
| Baseline | −7.897 / −20.786 dBFS | −6.141 / −16.559 dBFS | −1.187 / −15.250 dBFS |
| A6 | −7.897 / −20.786 dBFS | −9.035 / −19.453 dBFS | −2.624 / −17.150 dBFS |
| A5 | −7.897 / −20.786 dBFS | −8.026 / −18.444 dBFS | −2.150 / −16.538 dBFS |

These recomputed values agree with the values the owner reported for both rounds.

## 3. Reproduce

From the repository root:

```powershell
uv run python scripts/fl_studio/prepare_decision_packet.py --exports 'C:/uni/Bachelor-Thesis/test/trap-baseline' --out 'C:/uni/Bachelor-Thesis/test/agent-pilot-packet'
```

```powershell
uv run python scripts/fl_studio/verify_pilot_round.py --exports 'C:/uni/Bachelor-Thesis/test/trap-baseline' --candidate-prefix trap-agent-a6 --action A6 --packet-dir 'C:/uni/Bachelor-Thesis/test/agent-pilot-packet' --out 'C:/uni/Bachelor-Thesis/test/agent-pilot-run/verification-round1-a6.json'
```

```powershell
uv run python scripts/fl_studio/verify_pilot_round.py --exports 'C:/uni/Bachelor-Thesis/test/trap-baseline' --candidate-prefix trap-agent-A5 --action A5 --packet-dir 'C:/uni/Bachelor-Thesis/test/agent-pilot-packet' --out 'C:/uni/Bachelor-Thesis/test/agent-pilot-run/verification-round2-a5.json'
```

Add `--displayed-before <dB> --displayed-after <dB>` once the FL Studio readings are known. Repeated runs give byte-identical reports:
- `verification-round1-a6.json`: `f7aa67cd4fa5ebdf98054d5b6f378bbed37460ebe2e9c8d5a6f9e529d2ae0e95`
- `verification-round2-a5.json`: `cac102d294708f171478b20336877852d352f851c4f9850d991ff9be80e89581`

These are regenerated after the baseline-agreement gate was added (review fix), with unchanged results. Each report lists any `not_verified_reasons`; both lists are empty.

The packet script reproduces the packet, menu and verification sets byte-identically.

### Input hashes (SHA-256)

| File in `C:/uni/Bachelor-Thesis/test/trap-baseline/` | SHA-256 |
| --- | --- |
| `trap-baseline.flp` | `ac1880d9125e9176f1eb8bd30d1d0bfbe723870dfd1839a9eb4c34172fe3848b` |
| `trap-baseline_Kick.wav` / `_Bass.wav` / `_Master.wav` | `e445daeb…` / `74c82cd4…` / `c3badaf0…` |
| `trap-agent-a6.flp` | `4fc42a0885ec0b63bcf6c2d7cd134c5857f4387ff484dd7c554f6f4d0ffab272` |
| `trap-agent-a6_Kick.wav` / `_Bass.wav` / `_Master.wav` | `b7aa2ffb…` / `37a4900d…` / `e4404e14…` |
| `trap-agent-A5.flp` | `7bdf7a139ecaa7fc18e0f8568feabb3cbd81f78462abb92b871ca4bbdae45030` |
| `trap-agent-A5_Kick.wav` / `_Bass.wav` / `_Master.wav` | `10cf6ce2…` / `392280a3…` / `14a99f10…` |
| `C:/uni/Bachelor-Thesis/test/run-record.json` (round-1 response) | `aa58dcaa26d17dcd01cc50919467264a6f9b00a343469841abd96d314f0af0be` |

Full hashes of every export are in the two verification reports. The A6 and A5 kick exports differ in hash from the baseline kick, but they decode to bit-identical samples; FL Studio writes the project title into the WAV's INFO chunk. The original baseline files and the separate kick-cut trial files are unchanged.

## 4. Agent inputs and responses

**Round 1.**
- **Inputs:** the three bundle files and the exact request `agent-request.txt`, which asks for one action ID, a rationale tied to the baseline evidence and an uncertainty statement, and allows A0.
- **Response:** saved as parsed JSON in `test/run-record.json`, reproduced verbatim in §6. The raw reply text is not saved separately; the file holds the parsed object.
- **Valid menu ID:** yes (A6).

**Round 2.**
- **Inputs:** the round-1 context plus the owner's feedback message. The text below is as the owner pasted it into the development session on 2026-11-17. Whether it is byte-identical to what the agent received is **unknown**.

  > The first proposal, A6, was rendered in FL Studio. The measured 808 reduction was 2.894 dB, and the kick samples were unchanged. The primary 808 → kick check passed. The median margin fell by 2.894 dB across the 206 frozen baseline cells, and the number of cells above 0 dB fell from 206 to 199. The 808 RMS and master peak guards passed. The master RMS guard failed. Master RMS fell by 1.900 dB, beyond the allowed 1.5 dB drop. A6 was rejected. The original baseline is still the accepted state. Any new action will be applied to a fresh copy of that baseline using the same goal, menu and verification rules. Choose exactly one action ID from the original menu, including A0 if you prefer to abstain. Give a short rationale that uses the verification feedback and states what remains uncertain.

- **Response:** an **owner-supplied transcript recovered from the Codex development chat**, reproduced verbatim in §6.
  - **Saved as** `C:/uni/Bachelor-Thesis/test/agent-pilot-run/recovered-round-2-A5-response.txt`, SHA-256 `5dea341c6ab1a8a9a61efe9de12d7d7d3246f444bf06149922d01e848bd29bd6`. It is a byte-identical copy of the owner's recovered file in the Codex project folder.
  - **Provenance:** the owner posted the response in the separate Codex development chat, after the round-2 feedback prompt and before the A5 render. The file's header states it is a transcription of the response the owner supplied, not a direct export from the decision-model interface. JSON quotation marks are normalized, and line-continuation formatting is omitted.
  - **Content:** it parses as JSON with `action_id` **A5** (a valid menu ID), a rationale and an uncertainty statement. Uncertainty item 3 includes the statement that the agent lacked the exact primary criterion.
  - **Unverified:** the model identity, the original response timestamp, session isolation and the exact request as sent.
  - **Correction:** an earlier version of this report said the response was not available from the development conversation. It was available in the Codex development chat, which is a separate conversation from the Claude Code session that wrote this report.
- **Whether session isolation and context carry-over were the same as round 1:** **unknown**.

### Criteria actually supplied to the agent

| Rule | Round 1 | Round 2 |
| --- | --- | --- |
| Goal text | yes | yes (same) |
| Menu and execution note (manual; ±0.25 dB tolerance in `action-menu.json`) | yes | yes (same) |
| Primary criterion: frozen 206-cell set, median ≤ −1.0 dB, fewer positive cells | **no** | **no.** Only round 1's outcome was given ("passed", −2.894 dB, 206 → 199), not the threshold or the set definition |
| 808 RMS guard (≥ −3.25 dB) | no | outcome only ("passed"), no threshold |
| Master peak guard (≤ −1.0 dBFS) | no | outcome only ("passed"), no threshold |
| Master RMS guard (≥ −1.5 dB) | no | yes, implicitly: "beyond the allowed 1.5 dB drop" |
| Outcome logic (accept / roll back / not verified) | no | partly ("A6 was rejected; baseline still accepted") |

**Complete input needed for future runs:**
- the decision packet, the goal and the menu;
- the **full verification rule set** in machine-readable form, given to the agent before round 1:
  - the primary rule with its threshold and frozen-set definition (not the cells);
  - every guard with its threshold and measurement;
  - the execution tolerance and the outcome logic;
- for later rounds: the accepted state, and per earlier round its action, the measured values for **every** criterion with its threshold, and the outcome, as a structured round history;
- the exact request text per round, saved before sending.

Each round should also save the **raw response**, the model ID, the interface and parameters, and the time. Without these, a later run cannot show which information the choice used.

## 5. Unknown or missing evidence

| Item | Status | Effect on claims |
| --- | --- | --- |
| Round-2 agent response | **recovered:** an owner-supplied transcript from the Codex development chat (`recovered-round-2-A5-response.txt`, `5dea341c…`); not a direct interface export | A5 and its supplied rationale are documented. The text shows the feedback being used (A6's measured values, the master RMS failure). Because it is a transcript, it is evidence of what the owner recorded, not an interface log. |
| Model ID, interface, parameters and time, both rounds (including round 2's original timestamp) | **unknown / unverified** (`agent-pilot-run/run-record.json` is still blank; `test/run-record.json` holds only the parsed round-1 object; the round-2 transcript has no model or time) | Model attribution is not possible |
| Round-2 isolation and context (same session or new, what was attached) | **unknown** | Whether round 2 saw only the bundle, round 1 and the feedback is not documented |
| Exact round-2 message as sent | **unknown** (text known only as pasted to the development session) | Supplied criteria are reconstructed, not verified |
| Mixer insert indices and exact names (Kick, Bass) | **unknown** (not recorded) | Insert identity is inferred from the exports' names and from the kick staying bit-identical |
| Displayed FL Studio fader readings before and after, both rounds | **unknown** | The execution-tolerance rule cannot be evaluated; acceptance of A5 is on the measured criteria only |
| Listening comparison | **informal owner judgment recorded** (§8); playback level matching unconfirmed; no blind or level-matched comparison | The judgment is the owner's informal preference, not a controlled listening result |

## 6. Agent responses (verbatim)

### Round 1 (from `test/run-record.json`)

```json
{
  "action_id": "A6",
  "rationale": "I read the goal as 'make the kick more dominant than the bass'. The relevant evidence is the 808 -> kick direction, where the 808 masks the kick. That direction has 534 masker-dependent cells, 206 of them not low-energy flagged, against only 117 for kick -> 808. Its median margin is -34.45 dB, against -57.01 dB for kick -> 808. All four top 808 -> kick regions fall on the kick's tail, where the sustained 808 overlaps it: calls 399-405 (5.21-5.28 s, median margin 21.84 dB, max 77.81 dB), calls 5-11 (0.06-0.14 s, median 19.11 dB), calls 136-142 (1.77-1.85 s, median 19.38 dB) and calls 268-273 (3.49-3.56 s, median 18.87 dB). Each spans bands 0-20. The most reliable cells are in the low bands, which are the kick's main bands: band 0 (0-172 Hz) has 13 non-flagged masker-dependent cells, band 1 (172-302 Hz) 24, band 2 (302-474 Hz) 25, band 3 (474-603 Hz) 22 and band 4 (603-861 Hz) 20. The 808 is also the louder stem (RMS -16.56 dBFS against -20.79 dBFS for the kick; peak -6.14 dBFS against -7.90 dBFS). Turning the Bass (808) fader down lowers its level relative to the kick, which shrinks the 808 -> kick margins. The kick -> 808 margins are smaller (top regions have medians of 5-8 dB, for example calls 396-398, 265-266 and 134-135, in bands 2-20) and would only grow, which matches a kick-dominant balance. Cutting the kick (A1-A3) would work against the goal. I chose the largest 808 cut on the menu because the 808 -> kick region margins are around 19-22 dB. Even 3 dB will remove only a small share of those exceedances, so a smaller cut would be unlikely to change the balance measurably.",
  "uncertainty": "1) A 3 dB cut is far smaller than the region margins (median around 19-22 dB, max up to 77.81 dB). Most 808 -> kick exceedances will remain, so the model-measured effect may be small. 2) Many cells in the top regions are low-energy flagged (shares of 0.571-0.652), and the non-flagged cells in bands 17-20 number 0-1. Above roughly 7.7 kHz the evidence is therefore weak, and the case rests mainly on bands 0-4 in the kick-tail windows. 3) The kick onsets (for example calls 396-398) show kick -> 808 rather than 808 -> kick, so the attack may already dominate. The problem regions are in the tail, and a broadband fader cut is a blunt fix for them. 4) Margins are model exceedance under dist10, not proven perceptual masking. 5) A fader cut changes the whole 808 level, including stretches with no kick (for example 808-only calls 12-98 and 143-229). That thins the low end overall and lowers the master level, and any master-bus processing could change the outcome. 6) The goal is ambiguous: 'then bass' most likely means 'than bass', but it could mean 'kick first, then bass', which this menu cannot express. 7) The mixer insert index and name are still unconfirmed, and the 'Bass' insert must be the 808. 8) A 2 dB cut (A5) is a reasonable, more conservative alternative if the owner prefers to keep the 808's weight."
}
```

### Round 2 (owner-supplied transcript from the Codex development chat, `recovered-round-2-A5-response.txt`)

Header of the saved file, verbatim: "Source: copied from the owner's message in the Codex development chat, after the round-2 feedback prompt and before the A5 render. This is a transcription of the response supplied by the owner, not a direct export from the decision-model interface. Model identity, original response timestamp, session isolation and exact request as sent are unverified. JSON quotation marks are normalized below. Message line-continuation formatting is omitted."

```json
{
  "action_id": "A5",
  "rationale": "The round-1 feedback supports the direction of the change, but A6 was too large a step. Cutting the Bass (808) fader by 2.894 dB passed the primary 808 -> kick check. Over the 206 frozen cells, the median margin fell by the full 2.894 dB, and cells above 0 dB fell from 206 to 199. The kick was unchanged, and the 808 RMS and master peak guards passed. The only failure was the master RMS guard: -1.900 dB against an allowed -1.5 dB. The 808 dominates the master energy (stem RMS -16.56 dBFS against master -15.25 dBFS). If the stems sum linearly into the master, that measured pair implies the 808 carries about 73% of the master power. On that estimate, a 2 dB cut lowers master RMS by about 1.36 dB, which is inside the guard. A 1 dB cut lowers it by about 0.67 dB. The primary check should still pass: the margin fell in step with the cut, so A5 should lower the median 808 -> kick margin by about 2 dB. That happens in the same kick-tail regions as before (calls 5-11, 136-142, 268-273 and 399-405), with the reliable cells in bands 0-4 (0-861 Hz). A5 is the largest cut on the menu that is predicted to pass every guard. A cut of 3 dB or more is ruled out, and A4 would give up half the effect.",
  "uncertainty": "1) The master RMS prediction rests on one data point and assumes the stems sum linearly with no master-bus processing. The master peak of -1.187 dBFS leaves room for a limiter or compressor on the master. If one is there, the drop will not scale this way. 2) The predicted headroom is thin, about 0.14 dB. Execution tolerance is ±0.25 dB, and A6 landed 0.106 dB short of its nominal cut. A realised cut near -2.25 dB would give an estimated master RMS drop of about -1.51 dB and fail the guard. A cut between about -1.75 dB and -2.15 dB should pass. 3) A 2 dB cut leaves most 808 -> kick exceedances in place, since the region medians are about 19-22 dB. Cells above 0 dB will likely drop by fewer than the 7 seen with A6, so the gain toward the goal is modest. Whether the primary check passes depends on its exact criterion, which I don’t have. 4) The earlier caveats still apply. Above about 7.7 kHz the evidence is low-energy flagged. Margins are dist10 model exceedance, not proven perception. ‘Then bass’ is read as ‘than bass’. 5) If A5 fails the master RMS guard, A4 (-1 dB, estimated master drop about 0.67 dB) is the safer choice for the next round."
}
```

**Reading of the two responses** (descriptive; not a claim about the model in general):
- **Round 1** already named A5 as the more conservative alternative (uncertainty item 8), and its item 5 anticipated a master-level drop.
- **Round 2** uses the supplied feedback explicitly:
  - It cites A6's measured −2.894 dB, the 206 → 199 cells and the master RMS failure (−1.900 dB against −1.5 dB).
  - It estimates from the baseline stem and master RMS that the 808 carries about 73 % of the master power, and predicts a master RMS drop of about −1.36 dB for a 2 dB cut. The measured drop was −1.288 dB for a realised −1.885 dB cut.
  - It names the thin headroom against the execution tolerance, and its lack of the exact primary criterion.

The transcript documents this rationale as the owner recorded it. Its link to a specific model run cannot be verified.

## 7. Limits

- **One development mix** (a 6.9 s trap loop, kick and 808), one goal, two rounds. It is not evaluation data and supports no generalization.
- **Small menu:** cuts only, three steps per stem insert. The menu cannot express raises, EQ or time-local changes. A broadband fader cut moves the masking margins almost mechanically, so the primary check mostly confirms the sign and size of the change.
- **Manual execution in FL Studio:** the scripted bridge was not used. Insert indices and displayed fader readings were not recorded.
- **What the results show:** a proposal judged by fixed, measured acceptance rules, and a second proposal made after verification feedback that passed those rules. They do not show perceptual improvement: the margins are dist10 model exceedance. The owner's listening judgment (§8) is **informal**, with playback level matching unconfirmed.
- **Incomplete criteria supplied to the agent** (§4). The round-2 response exists only as an owner-supplied transcript, without model details or timestamp.
- **Pilot settings, not validated:** the thresholds, the 10 dB low-energy flag and the ±0.25 dB tolerance.

## 8. Owner listening judgment (informal)

**Recorded 2026-11-17, as stated by the owner:** A5 makes the kick more dominant, retains enough 808 weight, and is preferred because it sounds cleaner.

**Status:** an **informal judgment** by the producer, with **playback level matching unconfirmed**.
- A5 lowers master RMS by 1.288 dB, so a level difference may have influenced the preference.
- The comparison was not blind, and no repeated or second-listener comparison is recorded.

It is the owner's preference for this development mix, not a controlled listening result. It supports no claim beyond this mix.
