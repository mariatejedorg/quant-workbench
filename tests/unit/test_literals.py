from __future__ import annotations

import ast

import pytest

from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.domain.literals import (
    ValueKind,
    coerce_for,
    is_editable_literal,
    kind_of,
    parse_text,
    render_float,
    render_int,
    render_literal,
    render_str,
    same_literal,
)


# ------------------------------------------------------------------------ kinds
@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (None, ValueKind.NONE),
        (True, ValueKind.BOOL),  # bool must not be reported as int
        (3, ValueKind.INT),
        (3.0, ValueKind.FLOAT),
        ("x", ValueKind.STR),
        ((1,), ValueKind.TUPLE),
        ([1], ValueKind.LIST),
        ({1: 2}, ValueKind.DICT),
    ],
)
def test_kind_of(value: object, kind: ValueKind) -> None:
    assert kind_of(value) is kind


def test_unsupported_types_have_no_kind() -> None:
    assert kind_of({1, 2}) is None
    assert kind_of(b"x") is None
    assert kind_of(1j) is None


def test_editable_literals_are_recursive_and_finite() -> None:
    assert is_editable_literal({"a": [1, (2.5, None)], 3: "x"})
    assert not is_editable_literal(float("nan"))
    assert not is_editable_literal([1, float("inf")])
    assert not is_editable_literal({"a": {1, 2}})
    assert not is_editable_literal({frozenset(): 1})  # a key type with no literal form


def test_absurd_nesting_is_refused() -> None:
    value: object = 1
    for _ in range(20):
        value = [value]
    assert not is_editable_literal(value)


# ------------------------------------------------------------------ same_literal
def test_same_literal_keeps_int_float_and_bool_apart() -> None:
    assert not same_literal(1, 1.0)
    assert not same_literal(1, True)
    assert not same_literal(0.0, False)
    assert same_literal(1.5, 1.5)
    assert same_literal([1, (2, "a")], [1, (2, "a")])
    assert not same_literal([1], (1,))
    assert not same_literal([1, 2], [1, 2, 3])


def test_same_literal_compares_dicts_in_order() -> None:
    assert same_literal({"a": 1, "b": 2}, {"a": 1, "b": 2})
    assert not same_literal({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not same_literal({"a": 1}, {"a": 1.0})


# ---------------------------------------------------------------------- coerce
def test_coercion_is_strict_about_types() -> None:
    assert coerce_for(ValueKind.INT, 5) == 5
    with pytest.raises(UnsafeEditError, match="RATE is a int; got float"):
        coerce_for(ValueKind.INT, 5.5, name="RATE")
    with pytest.raises(UnsafeEditError, match="got bool"):
        coerce_for(ValueKind.INT, True)
    with pytest.raises(UnsafeEditError, match="got str"):
        coerce_for(ValueKind.FLOAT, "0.5")


def test_two_conveniences_int_for_float_and_list_for_tuple() -> None:
    result = coerce_for(ValueKind.FLOAT, 2)
    assert result == 2.0
    assert isinstance(result, float)
    assert coerce_for(ValueKind.TUPLE, [0.7, 1.3]) == (0.7, 1.3)


def test_a_none_constant_accepts_any_literal() -> None:
    assert coerce_for(ValueKind.NONE, "now") == "now"


def test_values_without_a_literal_form_are_refused() -> None:
    with pytest.raises(UnsafeEditError, match="cannot be written"):
        coerce_for(ValueKind.FLOAT, float("nan"))
    with pytest.raises(UnsafeEditError, match="cannot be written"):
        coerce_for(ValueKind.LIST, [{1, 2}])


# ------------------------------------------------------------------- parse_text
@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        (ValueKind.INT, " 42 ", 42),
        (ValueKind.INT, "10_000", 10_000),
        (ValueKind.FLOAT, "0.05", 0.05),
        (ValueKind.FLOAT, "1e-3", 0.001),
        (ValueKind.FLOAT, "3", 3.0),
        (ValueKind.STR, " AAPL ", " AAPL "),  # strings are taken verbatim
        (ValueKind.BOOL, "Yes", True),
        (ValueKind.BOOL, "off", False),
        (ValueKind.TUPLE, "(0.7, 1.3)", (0.7, 1.3)),
        (ValueKind.LIST, "['a', 'b']", ["a", "b"]),
        (ValueKind.DICT, "{'A': 1}", {"A": 1}),
    ],
)
def test_parse_text(kind: ValueKind, text: str, expected: object) -> None:
    assert same_literal(parse_text(kind, text), expected)


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (ValueKind.INT, "abc"),
        (ValueKind.INT, "1.5"),
        (ValueKind.FLOAT, "nan"),
        (ValueKind.FLOAT, ""),
        (ValueKind.BOOL, "maybe"),
        (ValueKind.TUPLE, "(1, "),
        (ValueKind.LIST, "(1, 2)"),  # right syntax, wrong container
        (ValueKind.DICT, "__import__('os')"),  # never evaluated: literal_eval only
    ],
)
def test_parse_text_rejects_bad_input_with_a_workbench_error(kind: ValueKind, text: str) -> None:
    with pytest.raises(UnsafeEditError):
        parse_text(kind, text)


# ------------------------------------------------------------------- rendering
def test_strings_keep_accents_but_escape_what_would_break_the_line() -> None:
    assert render_str("María") == '"María"'
    assert render_str('say "hi"') == '"say \\"hi\\""'
    assert render_str("it's", "'") == "'it\\'s'"
    assert render_str("a\nb\tc\\d") == '"a\\nb\\tc\\\\d"'
    assert render_str("\x00\x7f") == '"\\x00\\x7f"'
    assert render_str(chr(0x2028)) == '"\\u2028"'  # a line separator: escaped, stays one line
    assert render_str("\U0001f600") == '"\U0001f600"'  # printable emoji is kept


def test_floats_read_back_exactly_and_keep_a_tidy_exponent() -> None:
    assert render_float(0.1) == "0.1"
    assert render_float(2e-6) == "2e-6"
    assert render_float(1e-06) == "1e-6"
    assert render_float(1.5e22) == "1.5e22"
    assert render_float(100000.0, group_digits=True) == "100_000.0"
    assert render_float(0.25, group_digits=True) == "0.25"
    assert render_float(-2500.5, group_digits=True) == "-2_500.5"


def test_integers_can_keep_their_thousands_separator() -> None:
    assert render_int(10000) == "10000"
    assert render_int(10000, group_digits=True) == "10_000"
    assert render_int(999, group_digits=True) == "999"
    assert render_int(-1234567, group_digits=True) == "-1_234_567"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "None"),
        (True, "True"),
        (7, "7"),
        ((1,), "(1,)"),  # a one-element tuple needs its comma
        ((), "()"),
        ([1, "a"], '[1, "a"]'),
        ({"a": (1, 2)}, '{"a": (1, 2)}'),
    ],
)
def test_render_literal(value: object, text: str) -> None:
    assert render_literal(value) == text
    assert same_literal(ast.literal_eval(text), value)


def test_render_uses_the_requested_quote_everywhere() -> None:
    assert render_literal({"k": ["v"]}, quote="'") == "{'k': ['v']}"


def test_render_refuses_what_it_cannot_write() -> None:
    with pytest.raises(UnsafeEditError):
        render_literal({1, 2})
