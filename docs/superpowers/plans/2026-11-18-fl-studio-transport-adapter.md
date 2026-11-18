# FL Studio Transport Adapter Implementation Plan

> **For agentic workers:** use `superpowers:executing-plans` after owner approval, task by task, tests first. Preserve all existing uncommitted work. Leave changes uncommitted for review; no installation or live DAW calls.

**Status:** owner-approved implementation completed, uncommitted (2026-11-18). Runtime adoption, installation and live operation remain unauthorized by this plan.

**Goal:** implement `DawParameterController` over the reviewed FL MIDI protocol, with calibrated configuration, persistent dispatch intent, faithful replay and conservative uncertain outcomes.

**Architecture:** a new `adapters.fl_studio` component owns protocol, WinMM transport, exchange, serialization, SQLite command journal, profile and controller. Core contracts and the existing offline rendering path stay unchanged. This is unintegrated, operator-attended feasibility infrastructure until the separate owner scope decision D15 and later coordinator work.

**Tech stack:** existing Python >=3.11, standard library (`ctypes`, `sqlite3`, JSON, threading, time), pytest and uv; no new dependencies.

**Spec:** [approved FL adapter design](../specs/2026-11-18-fl-studio-action-adapter-design.md), [DAW control contract](2026-11-18-daw-control-contract.md), [ADR-030, Proposed](../../decisions/0030-daw-parameter-control-boundary.md), [bridge contract](../../research/fl-studio-bridge-contract.md), [conversion design](../../research/fl-studio-db-conversion-design.md).

## Global constraints and current state

- Planning baseline: HEAD `033b76dcbf1855f6e65bac646b4f6d8422e3d762`, with the coordinate/control slices and their docs uncommitted. Inspect the actual tree before implementation; do not discard or restage someone else's work. Reviewed focused result: 524 passed. Full-suite result supplied by implementer: 5,679 passed, 4 skipped; do not attribute that run to this planning task.
- No installed-script changes, FL calls, port enumeration on real devices, project saving, exporting or live trials. The default CLI remains unchanged.
- No runtime imports from `scripts/`, no cross-adapter dependencies, no imports of admission/loop/driver/evaluation from this adapter. Initializers are docstring-only.
- Recovery, recalibration, clearing uncertain operations, export continuation and coordinator integration are separate plans. The adapter exposes only inspect/apply, not normalized arbitrary writes or calibration probes.
- ADR-030 remains Proposed; D15, FR-14 and accepted scope/requirement tables are unchanged. A proposed storage ADR is part of this plan, not an accepted scope change.
- Run checks through `uv run --locked`; no staging, commits or push. Use disposable temporary files and controlled transports in tests.

## Decisions proposed by this plan

1. **Preserve historical experiments.** Keep `scripts/fl_studio/{bridge_protocol,bridge_client,device_bridge}.py` byte-identical to the implementation baseline. They are frozen research implementations. Copy the reviewed wire protocol into runtime ownership; runtime imports only its own package. Tests pin the source snapshot and compare relevant wire/conversion behavior. Historical duplication is explicit; this is not an alias or second active runtime owner. Later protocol changes require a new compatibility/version decision and deployment review.
2. **SQLite intent journal.** Use standard-library SQLite with `journal_mode=DELETE`, `synchronous=EXTRA`, explicit transactions and persistent command IDs. Commit pending intent before `send`. No claim that ordinary JSON append or Python flush alone satisfies the port's durable-intent obligation.
3. **One owner per session journal.** The journal is scoped to one configured MIDI endpoint pair/profile and held by one controller session. Use SQLite exclusive locking and serialize controller methods with an `RLock`. A second connection must fail before dispatch. Separate databases cannot police each other: configuration must give a DAW session one authoritative journal, not create a fresh database per attempt.
4. **Reconciliation stays explicit.** Inspect remains read-only and does not clear persisted unknown-write or conversion blocks. This intentionally differs from the old research CLI's automatic clearing on inspect; the approved design requires explicit controller reconciliation. That API is future work. Identical retries return stored outcomes without sending again, including after device-script reload.
5. **Strict core results win.** The wire rounds some differences to 1e-9; core validators use exact stored values. If a wire `applied` reply fails the stricter core check, return uncertain/invalid-reply and block further writes; never loosen core tolerances or relabel it as success.

These are engineering proposals for owner review, especially the new persistent store and the inspect/reconciliation distinction. Implementation starts only after this plan is approved.

## Review focus

1. Crash after dispatch but before outcome commit: restart must retain an unresolved intent and never resend it automatically.
2. Duplicate ID with a different revision, binding, profile, execution ID or action: reject even if the wire command happens to match.
3. Reply validates on the wire but fails the core contract: preserve uncertainty and conversion blocks rather than inventing confirmation.
4. Off-target result followed by inspect/restart: neither clears the blocked conversion; no new dB write.
5. Native MIDI timeout/error: buffers and handles remain valid until WinMM releases them; cleanup must not cause a use-after-free or overwrite the classified command outcome.

## Files and responsibilities

New under `src/autonomous_mixing/adapters/fl_studio/`:

| File | Responsibility |
| --- | --- |
| `__init__.py` | docstring only |
| `protocol.py` | frozen reviewed wire protocol and conversion, standard library only |
| `transport.py` | byte-transport interface, WinMM implementation, lazy native loading and safe resource ownership |
| `exchange.py` | one bounded request/reply exchange with injectable transport/clock; no retry or journaling |
| `profile.py` | exact tested profile factory and profile/command conversion checks |
| `serialization.py` | versioned canonical request identity and explicit JSON outcome encoding/decoding; no pickle |
| `journal.py` | SQLite command records, durable pending/outcome commits, replay and unresolved/conversion state |
| `controller.py` | implements the core port, owns ordering and mapping to typed outcomes |

New tests: `tests/unit/test_fl_runtime_protocol.py`, `test_fl_runtime_exchange.py`, `test_fl_runtime_journal.py`, `test_fl_runtime_controller.py`, `test_fl_runtime_transport.py`; architecture tests in `tests/architecture/test_fl_studio_adapter.py`, with only additive completeness updates in `test_adapters.py` as required.

Docs: proposed `docs/decisions/0031-fl-studio-control-journal.md` plus index; overview/runtime-adapter note; engineering-conventions layout only; research log append; this plan's checkboxes. Do not edit accepted ADRs, completed research captures or requirement statuses.

## Task 1 — Runtime wire protocol and calibrated profile

**Source snapshot SHA-256 at planning:**
- `bridge_protocol.py`: `03637001d7e953e5fe3a61a962f94c6c80f7bc01729d0e560cbac26ab373f485`
- `bridge_client.py`: `d8cbc877ee85743e88356b95321ffa1324629883aadf3f5298ec69baa6d85a52`
- `device_bridge.py`: `ce1af5bc3fcbd39c8e66fee6a7a576d8678a4f2f9e4b44ffdf80071a46c512ef`

These identify repository files, not the scripts currently installed or the bytes used in earlier live trials. If the implementation baseline differs, review that difference before extraction and record the new snapshot explicitly.

**Interfaces** in `profile.py`:
```python
def calibrated_profile(*, reference: VersionRef) -> DawControlProfile: ...
def make_apply_command(request: DawApplyRequest) -> dict: ...
```

Copy `bridge_protocol.py` to runtime `protocol.py` byte-identically initially; no project imports. Use its existing `inspect_command`, `set_gain_db_command`, `encode`, `decode`, `validate_reply`, `reply_consistency`, `h_db`, `db_target` and bounds helpers. The controller never exposes its calibration/normalized-write functions as capabilities.

Factory values: build `Producer Edition v26.1.7 [build 5654]`, API 45; title `Thesis Bass Bridge Test`, insert 2 `Bass`; conversion `VersionRef("fl-mixer-h125", "v1")` maps explicitly to wire `fl-mixer-h125-v1`; normalized [0.62,0.86]; dB bounds `h_db(0.62)`/`h_db(0.86)` without rounding; cuts [0.1,3.0]; start normalized/db tolerances .001/.05; success .05; normalized readback .001. Require exact equality of the supplied request profile to the factory result for its reference: caller declarations cannot widen the tested scope.

`make_apply_command` uses the resolution's observed reading and requested delta; recomputes the target through the versioned conversion, checks start-on-curve (.01 dB), both ranges and wire constraints before producing bytes. Core target semantics remain authoritative; any disagreement/refusal occurs before intent/send. Do not silently clamp, broaden the profile or accept another build.

- [x] Write failing parity, factory and command tests, then run them before adding modules.
- [x] Add protocol/profile code and pass: exact full-precision endpoints; explicit conversion-name mapping; wrong build/API/title/insert/profile/conversion rejected; actual request fixtures at nonzero baseline; start off-curve; target below/above range; non-ASCII wire limits; bytes identical to frozen research codec for inspect and valid dB commands.
- [x] Pin frozen script hashes and compare relevant runtime/research protocol behavior. Historical scripts remain unmodified.

## Task 2 — Byte exchange and WinMM transport

**Interfaces**:
```python
class MidiTransport(Protocol):
    def send(self, raw: bytes) -> None: ...
    def receive(self, timeout_s: float) -> bytes | None: ...
    def close(self) -> None: ...

class WinMMTransport:  # implements MidiTransport, context manager
    def __init__(self, out_name: str, in_name: str): ...

@dataclass(frozen=True)
class ExchangeResult:  # adapter-local
    status: str  # exactly replied, timeout, invalid_reply
    reply_json: str | None
    diagnostics: tuple[str, ...]

def exchange(command: dict, *, transport: MidiTransport,
             timeout_s: float, clock: Callable[[], float]) -> ExchangeResult: ...
```

Require finite non-bool positive timeout. No internally generated write ID, resend or transport retry. Use one monotonic deadline for send plus receive; cannot forcibly interrupt a blocked driver call, so deadlines remain cooperative. Ignore unrelated MIDI; validate and correlate IDs; malformed matching reply stops immediately as invalid_reply, never wait for a later good reply. Record bounded diagnostic summaries (at most 32 entries, each at most 1024 characters) so foreign traffic does not grow memory without bound. Structurally undecodable traffic without a readable matching ID is diagnostic only until deadline. A repeated non-advancing fake clock must be fixed in the test fixture, not replaced with wall-time sleeps.

Extract reviewed WinMM mechanics from `bridge_client.py` into `transport.py`, without CLI, sys.path manipulation, journal or core imports. Load DLL only when constructing a native transport; importing on POSIX must work. Configure pointer-width-safe ctypes prototypes. Exact port names required; reject ambiguous duplicate names rather than choosing the first. Close on all partial-construction failures. Keep input and output storage alive until completion or a successful reset returns ownership; check native cleanup results and retain outstanding buffers if release fails, including retaining the transport object in a poisoned controller. Never free an active outgoing buffer merely because a two-second wait ended. Close is idempotent. No real enumeration in tests.

- [x] Write tests first using a controlled byte transport and fake WinMM library, then implement.
- [x] Cover deadline expiry, receive returning None, stray IDs, malformed matching vs foreign frames, wire-inconsistent reply, bounded diagnostics, send/receive exceptions.
- [x] Cover partial open/prepare/start errors, duplicate names, delayed outgoing completion, reset/close failures and buffer ownership, pointer widths, import without DLL access.
- [x] Keep fake-driver tests distinct from live evidence. Live transport remains untested for this runtime implementation until separately authorized.

## Task 3 — Canonical identity and durable command journal

**Interfaces** (`serialization.py`):
```python
def request_json(request: DawApplyRequest) -> str: ...
def outcome_json(outcome: DawApplyOutcome) -> str: ...
def load_outcome(value: str) -> DawApplyOutcome: ...
```

Use explicit schema version 1, stable sorted JSON with `allow_nan=False`; encode all request fields, including execution ID, admitted policy reference, action/revision, complete binding/state/profile and inspection. No repr, Python class import paths, pickle, or hash-only identity. Store canonical content as well as its SHA-256. Decode outcomes by an allowlisted discriminator and reconstruct real validated core records. Do not alter achieved arithmetic on replay.

**Interfaces** (`journal.py`, adapter-local):
```python
@dataclass(frozen=True)
class JournalEntry:
    request_json: str
    command_json: str
    outcome: DawApplyOutcome | None  # None means pending

class SqliteCommandJournal:
    def __init__(self, path: Path, *, session_key: str): ...
    def lookup(self, command_id: str) -> JournalEntry | None: ...
    def reserve(self, command_id: str, request_json: str, command_json: str) -> None: ...
    def finish(self, command_id: str, outcome: DawApplyOutcome) -> None: ...
    def pending_or_uncertain(self) -> bool: ...
    def blocked_conversions(self) -> frozenset[VersionRef]: ...
    def close(self) -> None: ...
```

Use an existing configured local directory; no implicit network paths or `:memory:` runtime database. Store schema version/session key once; mismatches, malformed rows, unknown versions and failed integrity checks fail closed. One commands table holds immutable canonical request and wire command, nullable outcome and a checked command-ID primary key. `reserve` rejects an existing ID, unresolved earlier write or blocked conversion and commits pending before returning. `finish` updates only the matching pending row; an already stored outcome cannot be silently replaced. Derive conversion blocks from recorded off-target outcomes; derive unresolved state from pending/uncertain outcomes. No clear method in this slice.

Configure and read back `journal_mode=DELETE`, `synchronous=EXTRA`, `locking_mode=EXCLUSIVE`, busy timeout zero. Acquire the exclusive database lock during initialization and retain it until close. All writes use explicit commit/rollback, parameterized SQL, and serialization under the controller lock. Test second-connection exclusion; merely setting a PRAGMA string does not prove a lock exists.

**Durability claim:** dispatch may start only after SQLite reports a successful pending transaction commit under these verified settings. This relies on the local OS/filesystem/storage honoring SQLite synchronization; it is not an independent power-loss or hardware guarantee. Subprocess crash tests establish process-crash behavior only. SQLite documents `EXTRA` and exclusive locking in its [PRAGMA reference](https://www.sqlite.org/pragma.html#pragma_synchronous) and [locking-mode section](https://www.sqlite.org/pragma.html#pragma_locking_mode). JSON diagnostics are not the authority journal.

- [x] Write failing serialization, reserve/finish/reopen and exclusive-owner tests before implementation.
- [x] Test request differences beyond wire payload; strict decode; altered IDs/schema/session; pending survives reopen; confirmed/off-target/uncertain replay reconstruction; off-target block survives reopen/inspect; concurrent callers cannot reserve two writes.
- [x] Subprocess exits immediately after committed reserve; reopen must find pending. Subprocess exits after fake send but before finish; reopen must never trigger a send. Finish commit failure must leave pending authority, not a cached confirmed result.
- [x] Exercise injected commit failure with zero sends before reserve succeeds; verify PRAGMAs by querying, not mocking only their assignment.

## Task 4 — Port implementation and failure classification

**Interface** (`controller.py`):
```python
class FlStudioParameterController:
    def __init__(self, *, profile: DawControlProfile, journal: SqliteCommandJournal,
                 transport_factory: Callable[[], MidiTransport], timeout_s: float,
                 clock: Callable[[], float], next_inspect_id: Callable[[], str]): ...
    def inspect(self, request: DawInspectRequest) -> PortResult[DawInspection]: ...
    def apply(self, request: DawApplyRequest) -> DawApplyOutcome: ...
```

Caller owns journal lifetime; controller owns each factory-created transport and closes it. Serialize complete inspect/apply operations with an `RLock`. Constructor validates timeout and exact supported profile without opening MIDI ports. Journal session key binds profile and endpoint configuration; the wiring driver constructs it explicitly. Do not infer a unique live project file from its metadata title.

### Inspect

Check that the inspect request's profile is the exact configured supported profile before opening transport; an unsupported configuration returns `PortFailure(UNSUPPORTED)`. Build a fresh protocol inspect ID; never reuse an apply ID. Exchange and validate the reply. `ok` and `identity_mismatch` become `Success(DawInspection(...))`; mismatches carry no gain. Preserve observed build differences for the gate to reject. Invalid fields/core construction/response mismatch -> `PortFailure(INVALID_RESPONSE)`; timeout/open failure -> `UNAVAILABLE`; unsupported -> `UNSUPPORTED`. Use `retryable=True` only for transient read availability/timeout, otherwise False. A blank build string that core cannot represent is invalid response. Always call `validate_daw_inspection`. Inspect never clears pending/uncertain or conversion blocks and never changes stored write outcomes.

### Apply ordering

1. Canonicalize and validate input. Lookup command ID before opening transport or rechecking mutable state.
2. Different canonical request under an existing ID -> refused `DUPLICATE_ID_CONFLICT`, no send and no overwrite. Recorded terminal outcome -> core-validate against the identical original request and replay locally (written outcomes `replayed=True`; others unchanged). This holds even if the device script reloaded or state moved later.
3. Existing pending for this ID -> return uncertain `PENDING_UNRESOLVED`, never resend. A new ID while an earlier pending/uncertain write exists -> refused `UNAVAILABLE`, explaining the earlier unresolved operation. Blocked conversion -> refused `CONVERSION`. No inspect auto-clear.
4. Validate exact adapter profile, recompute/check conversion target and wire encode. Known local profile/curve/range/bounds failure -> appropriate typed pre-write refusal, no send. Transport open failure before reserve -> refused `UNAVAILABLE`.
5. Commit `reserve` successfully, then call exchange once. Any failure after intent may have committed is conservative uncertainty; do not guess whether bytes reached FL.
6. Map valid replies and run `validate_daw_apply_outcome` before journal finish. Persist outcome successfully before returning confirmed/off-target. Finish failure returns uncertain `DISPATCH_INTERRUPTED` and poisons this controller against further writes. A failure injected before commit must leave pending authority. A real commit error can be ambiguous: do not claim the transaction certainly failed; reopening must inspect the database. An intact validated committed outcome can be replayed, while pending/corrupt/unknown storage remains blocked. Under no storage-recovery branch is the write resent automatically.
7. Close transport on all paths. A cleanup failure after a validated and durably finished reply must not erase that evidence or rewrite the outcome; record bounded diagnostic state and refuse further dispatch on this controller until it is reopened. A cleanup failure without a trusted completed reply remains uncertain.

Keep in-memory replay of local pre-intent refusals and unpersisted fallback outcomes for this controller lifetime. Persist terminal refusals when journal is usable using a dedicated internal journal transaction (no pending write is implied). If storage cannot record a pre-intent refusal, return `UNAVAILABLE`, send nothing and poison writes on the instance; do not claim that this unavailable receipt survives restart. The port's stable replay guarantee across restart applies to successfully journaled outcomes; a storage-failure fallback is not a durable receipt, and its recovery limitation must be explicit in the proposed ADR and tests. A failure whose pending-commit success is ambiguous must remain uncertain/poisoned until stored state is checked, not assert nothing was written on a later retry.

### Mapping table

| Wire result | Core outcome |
| --- | --- |
| `applied` / `applied_off_target` | Confirmed / OffTarget only after wire, identity/build/profile, exact achieved difference and core validator pass |
| `identity_mismatch` | Refused IDENTITY_MISMATCH |
| `precondition_failed` | Explicit mapping: transport_playing, edit_unavailable, start_gain, range, uncalibrated_build, uncalibrated_start, cut_bounds to corresponding core refusal codes |
| `invalid_command` duplicate_id_conflict | Refused DUPLICATE_ID_CONFLICT |
| recognized conversion/conversion_mismatch/bounds errors | Corresponding pre-write refusal only where the reviewed script guarantees no write |
| `error` api_too_old / `unsupported` | Refused API_TOO_OLD / UNSUPPORTED |
| wire `uncertain` readback_disagrees | Uncertain READBACK_DISAGREES |
| other wire `uncertain` | Uncertain EXCEPTION_AFTER_WRITE |
| timeout / malformed or contradictory matching reply | Uncertain TIMEOUT / INVALID_REPLY |
| send/receive interruption, unknown status/error, post-reply construction or validator exception | Uncertain DISPATCH_INTERRUPTED or INVALID_REPLY, never generic pre-write refusal |

Recompute `achieved_change_db = after.db - before.db` from validated readings; first verify the wire's declared difference meets its own reviewed consistency check. Do not copy a merely approximately equal wire difference into core's exact-equality record. Include actual wire command target as `commanded_normalized`; do not substitute the read-back for the requested target. A reply reporting a different actual build after a write is uncertain, even if its fader values look plausible.

- [x] Write controller tests first using the real runtime exchange, protocol and journal with a controlled byte transport. Do not substitute the old test-only fake controller for this implementation.
- [x] Test every mapping row, stricter-core boundary mismatch, on-curve/range checks, nonzero application baseline, close failure and outcome-commit failure.
- [x] Test identical retries after confirmed/off-target/refused/uncertain and after reopening: zero additional sends; changed request under same ID refused; device reload does not cause resend; pending restart blocks.
- [x] Assert reserve commits before the transport sees a send; no send if reserve/open/profile fails; unknown-after-send cannot become refused; inspect cannot clear unknown or off-target state.

## Task 5 — Architecture, documentation and review evidence

- [x] Add exact edges and no-third-party restrictions for this component. Protocol has no project imports; transport owns only native/stdlib and protocol framing constants; exchange owns protocol/transport; serialization owns only required core records; journal owns serialization and required records; profile owns protocol plus core records; controller owns these adapter modules and core records/ports. Resolve actual imports explicitly in inventories; do not widen unrelated rules.
- [x] Fresh-process isolation: importing adapter package loads only its initializer; importing controller on Windows/POSIX loads no native DLL, devices, NumPy, soundfile, SciPy, admission, loop, provider SDK or research scripts. Completeness scans cover the new component and package list.
- [x] Draft ADR-031 (Proposed) documenting transactional dispatch intent, single authoritative session journal, local replay, limits, and explicit reconciliation deferral. It is a command journal, not the acceptance authority or durable RunJournal. Add its index row; leave ADR-030 Proposed and D15 pending.
- [x] Update overview and conventions layout with unintegrated status; append research log with actual red/green, failure injection, source hashes, independent review and missing live evidence. Mark only completed plan steps. Preserve earlier log lines.
- [x] Mutation probes, each restored byte-identically: send before reserve; accept mismatching command ID; replay a pending write by sending; clear conversion block on inspect; skip core response validator; return confirmation after finish-commit failure. Each must fail a permanent test.
- [x] Run `uv run --locked pytest tests/unit/test_fl_runtime_protocol.py tests/unit/test_fl_runtime_exchange.py tests/unit/test_fl_runtime_journal.py tests/unit/test_fl_runtime_controller.py tests/unit/test_fl_runtime_transport.py tests/unit/test_daw_control_contract.py tests/unit/test_daw_gain_coordinates.py tests/architecture tests/unit/test_public_contracts.py -q -W error`.
- [x] Run full `uv run --locked pytest -q -rs`, `uv run --locked ruff check .`, `uv run --locked ruff format --check src tests`, CLI help, `uv lock --check`, and `git diff --check`. Recheck frozen research hashes and existing offline/public tests unchanged apart from additive inventories.
- [x] Request independent review. Leave everything uncommitted. Report source changes separately from supplied/live historical evidence; no test-double result is a live validation claim.

## Subsequent work and owner decisions

Before exposing this adapter in a runnable driver, owner approval of D15/new requirement and ADR-030 adoption must be recorded explicitly. Before any new live trial: separate review of runtime extraction, installed controller/protocol compatibility, and explicit authorization for installation and trial. This plan grants neither.

Then plan exact-target recovery (including upward restoration and separate authorization/budget), explicit pending-operation reconciliation, export continuation/verified import, and coordinator integration. Until then an uncertain write blocks new actions; a confirmed fader write is never candidate-ready audio or accepted mixing improvement.

## Plan self-review

Interfaces are defined before consumption. The five review-focus risks have tests in Tasks 2–4. Runtime ownership and historical experiment preservation are explicit. Durability is tied to a checked transaction boundary and qualified storage assumptions, not append semantics. Current core outcome types and calibrated restrictions are preserved. New journal, replay, ownership and reconciliation choices remain proposals for plan approval. No implementation or live validation is claimed by this document.


## Implementation outcome (2026-11-18)

Owner approved execution in the development chat. Implemented unintegrated; no installation or live calls.
Final locked checks: 630 focused tests and 5785 full-suite tests passed, 4 platform skips.
No requirement/adoption status moved; ADR-030/031 remain Proposed and D15 open.

Implementation rulings: the journal schema also persists inspection IDs, because delayed/duplicate read IDs
must not collide with applies after reopening. Partial-constructor cleanup uses an explicit retained-resource
error, which poisons the controller. These review fixes have permanent red/green regressions. Native cleanup
failures and invalid/reused inspect IDs are nonretryable. No core port signatures were changed.
Subprocess crash evidence is not hardware/power-loss validation. Pending/uncertain and conversion blocks have
no clear/reconcile API in this slice. The fake-driver and controlled-byte tests are not live FL evidence.
Frozen trial source hashes and protocol byte identity were rechecked. Everything is left uncommitted.
