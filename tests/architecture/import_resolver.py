"""Test-only import resolution shared by the architecture tests (guarantee-ledger row G3).

``resolve_imports`` returns the modules a source file depends on. ``import a.b`` and
``from a.b import name`` resolve to ``a.b``; ``from a import b`` and ``from .. import b`` resolve to the
child module ``a.b`` when it exists, so package-form imports cannot hide a dependency behind their
package. A relative import that escapes the top-level package resolves to ``None``.
"""

import ast
from pathlib import Path


def module_files(src: Path, root: Path) -> dict[str, Path]:
    """Map every module below ``root`` (recursively, caches excluded) to its file."""
    files = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(src).with_suffix("").parts
        files[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
    return files


def missing_package_children(
    source: str, module: str, is_package: bool, packages: frozenset[str], known: frozenset[str]
) -> list[str]:
    """``from <package> import name`` targets where ``package`` is in ``packages`` and ``name`` is not a module.

    ``resolve_imports`` maps such a name to its package. For packages whose initializers define nothing
    (ADR-027 step M4), the name can only be a missing module, so callers report these as unresolved.
    """
    package = module.split(".") if is_package else module.split(".")[:-1]
    missing = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            if node.level - 1 >= len(package):
                continue
            base = ".".join(package[: len(package) - (node.level - 1)] + ([node.module] if node.module else []))
        else:
            base = node.module or ""
        if base in packages:
            missing += [f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" not in known]
    return missing


def resolve_imports(source: str, module: str, is_package: bool, known: frozenset[str]) -> set[str | None]:
    package = module.split(".") if is_package else module.split(".")[:-1]
    targets: set[str | None] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level - 1 >= len(package):
                    targets.add(None)
                    continue
                base = ".".join(package[: len(package) - (node.level - 1)] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            for alias in node.names:
                child = f"{base}.{alias.name}"
                targets.add(child if child in known else base)
    return targets
