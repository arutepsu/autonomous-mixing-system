"""Exact canonical JSON for core records (Increment 5, ADR-039).

Only the public frozen dataclasses and enums defined in ``autonomous_mixing.core.records`` can be encoded or
decoded: the registry is built from those modules, and nothing is ever imported by a name read from storage.
Decoding goes through each record's constructor, so every ``__post_init__`` invariant is re-checked on load.

Encoding: a record is ``{"$t": "<module>.<Class>", "f": {field: value}}``; an enum ``{"$e": "<module>.<Class>",
"v": value}``; a tuple is a JSON list; a frozenset ``{"$fs": [...]}`` sorted by each item's canonical text; a
timezone-aware datetime ``{"$dt": iso}`` (naive datetimes are refused); a float its shortest round-trip ``repr``
(``-0.0`` kept, non-finite refused); ``bool``, ``int``, ``str`` and ``None`` as themselves. Anything else is refused.
Callers that persist a record must also check ``decode(loads(dumps(encode(record)))) == record``.
"""

import dataclasses
import importlib
import json
import math
from datetime import datetime
from enum import Enum
from pathlib import Path

_RECORDS = "autonomous_mixing.core.records"
_RECORDS_DIR = Path(__file__).resolve().parents[2] / "core" / "records"


def _registry() -> dict[str, type]:
    """Every public dataclass and enum defined in a ``core.records`` module, keyed ``<module>.<Class>``."""
    found = {}
    for path in sorted(_RECORDS_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"{_RECORDS}.{path.stem}")
        for name, value in vars(module).items():
            if name.startswith("_") or not isinstance(value, type) or value.__module__ != module.__name__:
                continue
            if dataclasses.is_dataclass(value) or issubclass(value, Enum):
                found[f"{path.stem}.{name}"] = value
    return found


_TYPES = _registry()
_NAMES = {value: key for key, value in _TYPES.items()}


def encode(value: object) -> object:
    """The JSON-ready canonical form of ``value``; raises ``ValueError`` for anything that is not exactly encodable."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Enum):
        name = _NAMES.get(type(value))
        if name is None:
            raise ValueError(f"enum {type(value).__name__} is not a core record type")
        return {"$e": name, "v": value.value}
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a non-finite float is not exactly serializable")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("a naive datetime is not exactly serializable")
        return {"$dt": value.isoformat()}
    if isinstance(value, tuple):
        return [encode(item) for item in value]
    if isinstance(value, frozenset):
        return {"$fs": sorted((encode(item) for item in value), key=dumps)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = _NAMES.get(type(value))
        if name is None:
            raise ValueError(f"{type(value).__name__} is not a core record type")
        return {"$t": name, "f": {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    raise ValueError(f"{type(value).__name__} is not exactly serializable")


def decode(data: object) -> object:
    """Rebuild a value from its canonical form through the record constructors; raises ``ValueError``."""
    if data is None or isinstance(data, (bool, str, int, float)):
        return data
    if isinstance(data, list):
        return tuple(decode(item) for item in data)
    if not isinstance(data, dict):
        raise ValueError("not a canonical record form")
    keys = set(data)
    try:
        if keys == {"$dt"}:
            moment = datetime.fromisoformat(data["$dt"])
            if moment.tzinfo is None:
                raise ValueError("a stored datetime lost its offset")
            return moment
        if keys == {"$fs"}:
            return frozenset(decode(item) for item in data["$fs"])
        if keys == {"$e", "v"}:
            cls = _TYPES.get(data["$e"])
            if cls is None or not issubclass(cls, Enum):
                raise ValueError(f"unknown enum tag {data['$e']!r}")
            return cls(data["v"])
        if keys == {"$t", "f"}:
            cls = _TYPES.get(data["$t"])
            if cls is None or not dataclasses.is_dataclass(cls) or not isinstance(data["f"], dict):
                raise ValueError(f"unknown record tag {data['$t']!r}")
            names = {f.name for f in dataclasses.fields(cls) if f.init}
            if set(data["f"]) != names:
                raise ValueError(f"stored fields of {data['$t']} differ from the record's")
            return cls(**{name: decode(value) for name, value in data["f"].items()})
    except (TypeError, KeyError) as error:
        raise ValueError(f"invalid canonical record form: {error}") from error
    raise ValueError("not a canonical record form")


def dumps(data: object) -> str:
    """Canonical text: sorted keys, no whitespace, no NaN."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def loads(text: str) -> object:
    return json.loads(text)
