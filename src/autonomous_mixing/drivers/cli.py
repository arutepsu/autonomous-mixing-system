import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from autonomous_mixing.adapters.dsp_analysis.analysis import analyze_pair


def _report_conflict(output: Path, inputs: tuple[str, ...]) -> str | None:
    """Return why ``output`` cannot safely receive the report, or None if it is separate.

    Compares filesystem identity (device and file ID, following symlinks), so relative
    paths, symlinks and hard links that name an input are detected. A nonexistent
    destination has nothing to compare. This is a pre-write check, not a lock: it does
    not guard against concurrent changes between the check and the write.
    """
    try:
        output_stat = os.stat(output)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot verify that report output {output} is separate from the inputs: {exc}"
    for name in inputs:
        try:
            input_stat = os.stat(name)
        except OSError as exc:
            return f"cannot verify that report output {output} is separate from input {name}: {exc}"
        if os.path.samestat(output_stat, input_stat):
            return f"report output {output} refers to input file {name}; choose a different --output"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonomous-mixing")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="Run deterministic descriptive kick/bass analysis")
    analyze.add_argument("kick")
    analyze.add_argument("bass")
    analyze.add_argument("--output", default="results/analysis.json")
    _add_daw(sub)
    args = parser.parse_args(argv)
    if args.command == "daw":
        return _daw(args)
    if args.command == "analyze":
        try:
            result = analyze_pair(args.kick, args.bass)
        except ValueError as exc:
            # Input validation failures are user errors: report them without a traceback.
            print(f"error: {exc}", file=sys.stderr)
            return 2
        path = Path(args.output)
        inputs = (args.kick, args.bass)
        # Check before creating the parent and again before writing: on POSIX a path such
        # as "missing/../input.wav" only resolves to the input once "missing" exists.
        conflict = _report_conflict(path, inputs)
        if conflict is None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except (FileExistsError, NotADirectoryError, PermissionError, FileNotFoundError) as exc:
                # E.g. a parent component is an existing file. Directories created
                # before the failure are left in place.
                conflict = f"cannot create report directory {path.parent}: {exc}"
            else:
                conflict = _report_conflict(path, inputs)
        if conflict is not None:
            print(f"error: {conflict}", file=sys.stderr)
            return 2
        path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(f"Wrote {path}")
    return 0


# The declared fader-literal grammar (R5C C2): plain decimals with an optional exponent; no sign on positive values,
# no leading dot, no leading zeros, no underscores, no hexadecimal, no ``nan``/``inf``.
_DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?")
CREDENTIAL_VARIABLE = "ANTHROPIC_API_KEY"
CONTINUE_UNAVAILABLE = (
    "daw continue is not yet available (R5C C3): continuing a suspended run needs the operator manifest and "
    "attestation handling of the next increment. Nothing was opened or changed; 'daw status' shows stored runs."
)


def _add_daw(sub) -> None:
    """The operator DAW commands (ADR-040, ADR-043): start (R5C C2), status and recover; continue follows in C3."""
    daw = sub.add_parser("daw", help="Operator tooling for the FL Studio DAW path (ADR-040)")
    commands = daw.add_subparsers(dest="daw_command", required=True)
    start = commands.add_parser(
        "start",
        help="Start an operator-assisted DAW agent run up to its first manual-export suspension",
        description="Start one run from a tool configuration and a run configuration. The provider credential is "
        f"read from the {CREDENTIAL_VARIABLE} environment variable after both files validate; it is never stored or "
        "printed. Saving and exporting in FL Studio stay manual.",
    )
    # argparse reads '-1e-05' as an option unless its private negative-number pattern covers the declared grammar
    # (Python 3.11 default: '^-\d+$|^-\d*\.\d+$'); this parser has no option that looks like a negative number.
    start._negative_number_matcher = re.compile(r"^-(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
    start.add_argument("--config", required=True, help="the tool configuration (stores, FL journal, MIDI, roots)")
    start.add_argument("--run-config", required=True, help="the run configuration (schema 1, ADR-043)")
    start.add_argument("--run-id", required=True, help="a new run ID: 1-64 letters, digits, '-' or '_'")
    start.add_argument("--accept-current-fader", nargs=2, metavar=("NORMALIZED", "DB"),
                       help="first session only: repeat the fresh reading shown by a previous attempt exactly")  # fmt: skip
    proceed = commands.add_parser("continue", help="Continue a suspended run (not yet available: R5C C3)")
    proceed.add_argument("--config", required=True)
    proceed.add_argument("--run-id", required=True)
    proceed.add_argument("--attestation", required=True)
    status = commands.add_parser("status", help="Show stored runs and FL journal facts (read-only)")
    status.add_argument("--config", required=True)
    status.add_argument("--run-id")
    status.add_argument("--json", action="store_true")
    recover = commands.add_parser("recover", help="Show, then explicitly authorize, one recovery operation")
    recover.add_argument("--config", required=True)
    target = recover.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id")
    target.add_argument("--orphans", action="store_true", help="FL commands no run explains")
    recover.add_argument("--authorize", metavar="FINGERPRINT", help="the fingerprint phase 1 showed")
    recover.add_argument("--operation", type=int, metavar="INDEX", help="the index of one shown option")
    recover.add_argument("--operator", help="who authorizes the operation")
    recover.add_argument("--json", action="store_true")


def _daw(args) -> int:
    # Imported here so that 'analyze' never loads the DAW adapters.
    from autonomous_mixing.drivers import daw_commands
    from autonomous_mixing.drivers.daw_runtime import ConfigError, load_config

    if args.daw_command == "continue":
        print(f"error: {CONTINUE_UNAVAILABLE}", file=sys.stderr)  # before opening anything
        return 2
    if args.daw_command == "start":
        return _start(args)
    try:
        config = load_config(Path(args.config))
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        if args.daw_command == "status":
            shown = daw_commands.status(config, args.run_id)
            if args.json:
                print(json.dumps(shown if args.run_id is None else shown[0], indent=2))
            else:
                print("\n".join(f"{s['run_id']}: {s['classification']} - {s['explanation']}" for s in shown))
            return (
                4
                if any(
                    s["classification"] in ("in_use", "unsupported", "unavailable", "provider_evidence_unavailable")
                    for s in shown
                )
                else 0
            )
        run_id = None if args.orphans else args.run_id
        if args.authorize is None:
            shown = daw_commands.recover_view(config, run_id)
            print(json.dumps(shown, indent=2) if args.json else _describe(shown))
            return 0
        if args.operation is None or not args.operator:
            print("error: --authorize needs --operation and --operator", file=sys.stderr)
            return 2
        outcome = daw_commands.recover_apply(config, run_id, args.authorize, args.operation, args.operator)
        print(f"performed: {type(getattr(outcome, 'payload', outcome)).__name__}")
        return 0
    except (daw_commands.RecoveryRefused, daw_commands.StoreInUse) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 4
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _fader_literal(text: str, name: str) -> float:
    if not _DECIMAL.fullmatch(text):
        raise ValueError(f"--accept-current-fader {name}: {text!r} is not a plain decimal literal")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"--accept-current-fader {name}: {text!r} is not finite")
    return value


def _start(args) -> int:
    """S0-S3 here (arguments, both files loaded once, credential), then ``daw_commands.start``; exit 0/2/4."""
    from autonomous_mixing.core.records.daw_binding import DawFaderReading
    from autonomous_mixing.drivers import daw_commands
    from autonomous_mixing.drivers.agent_runtime import RuntimeRefusal
    from autonomous_mixing.drivers.daw_runtime import ConfigError, load_config, load_run_config

    def refused(message: str) -> int:
        print(f"REFUSED: {message} ({daw_commands.REFUSED_CLAUSE})", file=sys.stderr)
        return 4

    try:
        accept = None
        if args.accept_current_fader is not None:
            normalized, db = args.accept_current_fader
            accept = DawFaderReading(_fader_literal(normalized, "NORMALIZED"), _fader_literal(db, "DB"))
        config = load_config(Path(args.config))
        config.store_path(args.run_id)
        run = load_run_config(Path(args.run_config), config)
    except ValueError as error:  # ConfigError and literal/record validation: the operator's input is invalid
        print(f"error: {error}", file=sys.stderr)
        return 2
    api_key = os.environ.get(CREDENTIAL_VARIABLE)
    if api_key is None or not api_key.strip():
        print(f"error: no provider credential: set {CREDENTIAL_VARIABLE} (it is never stored or printed)",
              file=sys.stderr)  # fmt: skip
        return 2
    try:
        outcome = daw_commands.start(config, run, args.run_id, accept, api_key)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (RuntimeRefusal, daw_commands.StoreInUse, daw_commands.RecoveryRefused) as error:
        return refused(str(error))
    except daw_commands.StartRefused as error:
        retained = (
            f"; runtime artifacts retained at {error.retained}; a new run ID is required" if error.retained else ""
        )
        return refused(f"{error.stage} failed ({error.error_type}){retained}")
    except daw_commands.RunStopped as error:
        lines = [f"STOPPED: run {error.run_id} raised {error.error_type} after it started. It may have dispatched a "
                 "decision, written the fader or recorded an acceptance; its recorded state is uncertain. Inspect "
                 "with 'daw status'; use 'daw recover' if it reports an interruption.", *error.last]  # fmt: skip
        if error.cleanup_type is not None:
            lines.append(f"cleanup failed ({error.cleanup_type})")
        print("\n".join(lines), file=sys.stderr)
        return 4
    print(daw_commands.describe_outcome(outcome.result, run_id=args.run_id, tool_path=args.config))
    if outcome.cleanup_type is not None:
        print(f"cleanup failed ({outcome.cleanup_type}): the recorded outcome above stands; inspect with "
              "'daw status' before any further step", file=sys.stderr)  # fmt: skip
        return 4
    return 0


def _describe(shown: dict) -> str:
    lines = [f"state: {shown['classification']} - {shown['explanation']}"]
    if shown.get("proposed_target") is not None or shown.get("saved_target") is not None:
        lines.append(f"proposed target (journaled start): {shown['proposed_target']}; saved target: "
                     f"{shown['saved_target']}; exactly equal: {shown['exact']}")  # fmt: skip
    for index, option in enumerate(shown["options"]):
        lines.append(
            f"  [{index}] {option['operation']} {option['subject_command_id'] or ''} - {option['explanation']}"
        )
    if shown.get("fingerprint"):
        lines.append(f"fingerprint: {shown['fingerprint']}")
        lines.append("authorize one option with --authorize FINGERPRINT --operation INDEX --operator NAME")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
