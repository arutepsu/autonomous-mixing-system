import argparse
import json
import os
import sys
from pathlib import Path

from .audio.analysis import analyze_pair


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
    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    sys.exit(main())
