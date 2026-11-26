# First HT01 agent trial: offline preparation — 2026-11-26

Status: configuration and real-data preflight prepared, not authorized for live execution. No production code changed. Owner approved the intent and numerical level checks; provider-model lookup and routing declaration followed. Runtime package: `C:/uni/Bachelor-Thesis/test/HT01-engineering-trial-001/`.

The package has tool.json/run.json and six hash-identical WAV copies with neutral names. Only inputs/ is an input root. Private source mapping, actual analysis/diagnosis and review notes live separately in offline-check/. Reference/injection materials and project files were not copied into inputs. Stores, journal and export directories remain empty.

See the complete settings/limits review at `C:/uni/Bachelor-Thesis/test/HT01-engineering-trial-001/offline-check/README.md` and machine-readable validation.json /summary.json alongside it. Tool hash35d199f4a35d3997897d842c5792a1a7ffd8ac997eed26c90f5e47e7064fdbfc; run hash85a3cbe3da22fde97653d9a2d2d72334ca0fd9dad71adc3c80c5a6686a784288.

## Evidence and outstanding scope

- Tool/run parsing, typed provisional request and existing no-transport preflight pass; source/copy hashes verified unchanged. No provider credential was accessed. No run store, FL journal or MIDI transport was created.
- Baseline whole-clip Bass RMS -22.909727517251447dBFS; approved engineering target -23.159727517251447, minimum improvement0.25dB, maximum Bass RMS change1dB, Kick delta0.1dB and Master sample-peak ceiling -1dBFS. One attempt/write, one decision, no retries, three recovery operations,600s active budget. These are execution-test criteria, not proof of musical improvement. Required offline-processing/recipe records are explicitly marked unused/logical; no native Master=sum(stems) claim is made.
- Actual unchanged experimental P1 rule:1931 frozen cells,448 robust (23.20% <25%), longest run7 calls >=4. ASSESSED but zero candidates; P2/P3 UNAVAILABLE. No window or threshold was adjusted after measuring this. The first provider call may abstain or return a refused proposal; no write is guaranteed.
- Owner confirmed Bass routes only toMaster, incoming Kick->Bass EQ controller unchanged. Current handoff refreshes Bass/Master, not Kick. The configured Kick guard uses the retained baseline, so it cannot verify freshly exported live Kick. Corrective-trial approval must explicitly settle this limitation, without fabricating a routing dependency.
- Proposed model claude-sonnet-4-6 is an active legacy choice compatible with the reviewed temperature-bearing transport; current Sonnet5.5 rejects temperature. Official docs checked2026-11-26; links and rate/limit discussion are in the external review note. Provider access and billing remain unverified, and no paid call was made.

Next: review the prepared configuration and whether to authorize a first honest abstention-path trial under the unchanged policy. Installation/profile work is complete; no live start is authorized here. A later correction trial needs the fresh-Kick verification decision. Avoid expanding the adapter merely to force a first successful correction.

## Decision interface v2 after `ht01-observe-001` (added 2026-11-26)

The first live call (`ht01-observe-001`) ended as `invalid_response` with no write; that run and its audit stay unchanged and terminal. The owner approved policy A (proposals require a supplied diagnosis problem candidate) and the interface changes of [ADR-045](../../decisions/0045-decision-interface-v2-and-diagnosis-required-proposals.md) (Proposed): packet, prompt and response schema version 2, per-call alias-restricted provider schemas, explicit `decision_options` including exhausted execution budgets, categorized parser failures, and version-labelled refusal of v1 records. Implemented with regression tests that fail against the previous code; uncommitted for review; full suite and ruff pass.

Consequences for this trial: a new run started with this code binds the v2 versions; the HT01 situation (zero candidates) offers only `no_change` and `abstain`. Objective-driven actions (policy B) are a separate design decision, not a workaround to force a write. A further live call needs separate owner authorization; one call would test only reachability and response validity, not provider reliability.
