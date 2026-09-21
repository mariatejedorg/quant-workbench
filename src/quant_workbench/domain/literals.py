"""Python literal values: classify, compare, validate, parse from text and render as source.

A project's configuration is a handful of module-level constants such as ``TICKER = "AAPL"``
or ``MONEYNESS_RANGE = (0.7, 1.3)``. The config editor only touches constants whose value is
a *literal* (numbers, strings, booleans, ``None`` and tuples/lists/dicts of them), because a
literal can be rewritten without understanding any code. This module is the pure,
dependency-free part of that: which values are allowed and how each one is written back.
"""

from __future__ import annotations

import ast
import math
from enum import StrEnum
from typing import TypeAlias

from quant_workbench.domain.errors import UnsafeEditError

LiteralValue: TypeAlias = (
    "int | float | str | bool"
    " | tuple[LiteralValue, ...] | list[LiteralValue] | dict[LiteralValue, LiteralValue] | None"
)

#: Nesting deeper than this is refused: real configs are two levels at most, and a bound
#: keeps the recursive helpers safe from pathological input.
MAX_DEPTH = 8


class ValueKind(StrEnum):
    """The type of a configuration constant, as far as the editor is concerned."""

    INT = "int"
    FLOAT = "float"
    STR = "str"
    BOOL = "bool"
    NONE = "none"
    TUPLE = "tuple"
    LIST = "list"
    DICT = "dict"

    @property
    def is_container(self) -> bool:
        return self in (ValueKind.TUPLE, ValueKind.LIST, ValueKind.DICT)


#: ``bool`` precedes ``int`` because ``True`` *is* an ``int`` in Python: first match wins.
_KIND_BY_TYPE: tuple[tuple[type, ValueKind], ...] = (
    (bool, ValueKind.BOOL),
    (int, ValueKind.INT),
    (float, ValueKind.FLOAT),
    (str, ValueKind.STR),
    (tuple, ValueKind.TUPLE),
    (list, ValueKind.LIST),
    (dict, ValueKind.DICT),
)


def kind_of(value: object) -> ValueKind | None:
    """The :class:`ValueKind` of ``value``, or ``None`` when it is not an editable literal."""
    if value is None:
        return ValueKind.NONE
    return next((kind for kind_type, kind in _KIND_BY_TYPE if isinstance(value, kind_type)), None)


def is_editable_literal(value: object, *, _depth: int = 0) -> bool:
    """Whether ``value`` (recursively) only uses the literal types the editor can write."""
    if _depth > MAX_DEPTH or kind_of(value) is None:
        return False
    if isinstance(value, float):
        return math.isfinite(value)  # ``nan`` and ``inf`` have no literal form
    if isinstance(value, tuple | list):
        return all(is_editable_literal(item, _depth=_depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, int | float | str | bool | tuple | None)
            and is_editable_literal(key, _depth=_depth + 1)
            and is_editable_literal(item, _depth=_depth + 1)
            for key, item in value.items()
        )
    return True


def same_literal(left: object, right: object) -> bool:
    """Strict equality: ``1``, ``1.0`` and ``True`` are *different* literals here.

    Python says ``1 == 1.0 == True``; for an editor that would be a bug (changing an int to
    a float is a change). Dicts are compared in order, since the order is what gets written.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, tuple | list) and isinstance(right, tuple | list):
        return len(left) == len(right) and all(
            same_literal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return len(left) == len(right) and all(
            same_literal(k1, k2) and same_literal(v1, v2)
            for (k1, v1), (k2, v2) in zip(left.items(), right.items(), strict=True)
        )
    return bool(left == right)


# --------------------------------------------------------------------- coercion
def coerce_for(kind: ValueKind, value: object, *, name: str = "value") -> LiteralValue:
    """Check that ``value`` may replace a constant of type ``kind`` and normalise it.

    The rules are strict on purpose (an ``int`` setting must stay an ``int``), with the two
    conveniences a human expects: an ``int`` is accepted for a ``float`` constant, and a
    ``list`` for a ``tuple`` one (that is what JSON gives us).
    """
    if kind is ValueKind.NONE:
        accepted = value
    elif kind is ValueKind.FLOAT and isinstance(value, int) and not isinstance(value, bool):
        accepted = float(value)
    elif kind is ValueKind.TUPLE and isinstance(value, list):
        accepted = tuple(value)
    elif kind_of(value) is kind:
        accepted = value
    else:
        got = kind_of(value)
        raise UnsafeEditError(
            f"{name} is a {kind.value}; got {got.value if got else type(value).__name__}:"
            f" {value!r}",
            hint="Provide a value of the same type as the current one.",
        )
    if not is_editable_literal(accepted):
        raise UnsafeEditError(
            f"{name}: {accepted!r} cannot be written as a Python literal",
            hint="Supported: numbers, strings, booleans, None and tuples/lists/dicts of them.",
        )
    return accepted  # type: ignore[return-value]  # is_editable_literal proved the shape


_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


def parse_text(kind: ValueKind, text: str, *, name: str = "value") -> LiteralValue:
    """Interpret command-line ``text`` as a value of type ``kind`` (``qw config set``)."""
    try:
        if kind is ValueKind.INT:
            return coerce_for(kind, int(text.strip()), name=name)
        if kind is ValueKind.FLOAT:
            return coerce_for(kind, float(text.strip()), name=name)
        if kind is ValueKind.STR:
            return text
        if kind is ValueKind.BOOL:
            lowered = text.strip().lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
            raise ValueError(text)
        return coerce_for(kind, ast.literal_eval(text.strip()), name=name)
    except UnsafeEditError:
        raise
    except (ValueError, SyntaxError, MemoryError, RecursionError) as error:
        raise UnsafeEditError(
            f"{name}: {text!r} is not a valid {kind.value}",
            hint='Containers use Python syntax, e.g. "(0.7, 1.3)" or "{\'A\': 1}".',
        ) from error


# -------------------------------------------------------------------- rendering
_LATIN1_MAX = 0xFF  # up to here a character is escaped as \xNN
_BMP_MAX = 0xFFFF  # up to here as \uNNNN, above as \UNNNNNNNN
_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def render_str(value: str, quote: str = '"') -> str:
    """``value`` as a single-line string literal delimited by ``quote``.

    Anything that is not a printable character (control characters, line separators,
    unpaired surrogates) is escaped, so the result is always one physical line and always
    encodable in UTF-8. Accented letters and other printable text are kept as they are.
    """
    pieces = [quote]
    for char in value:
        if char == quote:
            pieces.append("\\" + quote)
        elif char in _ESCAPES:
            pieces.append(_ESCAPES[char])
        elif char.isprintable():
            pieces.append(char)
        else:
            code = ord(char)
            if code <= _LATIN1_MAX:
                pieces.append(f"\\x{code:02x}")
            elif code <= _BMP_MAX:
                pieces.append(f"\\u{code:04x}")
            else:
                pieces.append(f"\\U{code:08x}")
    pieces.append(quote)
    return "".join(pieces)


#: ``1000`` is the smallest number for which a thousands separator is worth writing.
_GROUPING_THRESHOLD = 1000


def render_int(value: int, *, group_digits: bool = False) -> str:
    """``10000`` or, when the original was written ``10_000``, ``10_000``."""
    return f"{value:_}" if group_digits and abs(value) >= _GROUPING_THRESHOLD else str(value)


def render_float(value: float, *, group_digits: bool = False) -> str:
    """The shortest text that reads back as exactly ``value``.

    ``repr`` already gives that, apart from exponents (``1e-06``); we drop the padding zeros
    and ``+`` so a constant written ``1e-6`` stays ``2e-6`` rather than ``2e-06``.
    """
    text = repr(value)
    if "e" in text:
        mantissa, _, exponent = text.partition("e")
        sign = "-" if exponent.startswith("-") else ""
        return f"{mantissa}e{sign}{exponent.lstrip('+-').lstrip('0') or '0'}"
    if group_digits:
        whole, dot, fraction = text.partition(".")
        return f"{int(whole):_}{dot}{fraction}" if abs(value) >= _GROUPING_THRESHOLD else text
    return text


def render_literal(
    value: object, *, quote: str = '"', group_digits: bool = False, _depth: int = 0
) -> str:
    """Python source for ``value``, on one line. ``ast.literal_eval`` reads it back exactly."""
    if _depth > MAX_DEPTH or not is_editable_literal(value):
        raise UnsafeEditError(f"{value!r} cannot be written as a Python literal")

    def child(item: object) -> str:
        return render_literal(item, quote=quote, group_digits=group_digits, _depth=_depth + 1)

    text: str
    if isinstance(value, tuple):
        inner = ", ".join(child(item) for item in value)
        text = f"({inner},)" if len(value) == 1 else f"({inner})"
    elif isinstance(value, list):
        text = "[" + ", ".join(child(item) for item in value) + "]"
    elif isinstance(value, dict):
        text = "{" + ", ".join(f"{child(k)}: {child(v)}" for k, v in value.items()) + "}"
    else:
        text = _render_scalar(value, quote, group_digits)
    return text


def _render_scalar(value: object, quote: str, group_digits: bool) -> str:
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, int):
        return render_int(value, group_digits=group_digits)
    if isinstance(value, float):
        return render_float(value, group_digits=group_digits)
    if isinstance(value, str):
        return render_str(value, quote)
    raise UnsafeEditError(f"{value!r} cannot be written as a Python literal")  # pragma: no cover
