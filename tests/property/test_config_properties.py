"""Property-based tests of the config editor (hypothesis).

Example-based tests check the cases somebody thought of. These generate thousands of values,
including the awkward ones (quotes, backslashes, control characters, huge and tiny floats,
nested containers) and check the invariants that must hold for *all* of them:

* every literal renders to text that reads back as exactly the same value;
* editing a constant changes that constant and **nothing else** in the file, on the real
  configuration files of the portfolio, with both Unix and Windows line endings.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from quant_workbench.domain.config import Constant
from quant_workbench.domain.literals import (
    LiteralValue,
    ValueKind,
    render_literal,
    same_literal,
)
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor

pytestmark = pytest.mark.property

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "real_configs"
REAL_SOURCES = {path.name: path.read_text(encoding="utf-8") for path in FIXTURES.glob("*.py")}

editor = LibCstConfigEditor()

#: file -> names of its scalar constants (project 1 only has a dict, so it is absent)
SCALAR_CONSTANTS = {
    file: [c.name for c in editor.constants(text) if not c.kind.is_container]
    for file, text in REAL_SOURCES.items()
}
SCALAR_CONSTANTS = {file: names for file, names in SCALAR_CONSTANTS.items() if names}

# ------------------------------------------------------------------- strategies
finite_floats = st.floats(allow_nan=False, allow_infinity=False)
scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**30), max_value=10**30),
    finite_floats,
    st.text(max_size=20),
)
hashable_keys = st.one_of(st.text(max_size=8), st.integers(-100, 100), st.booleans(), st.none())


def containers(children: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    return st.one_of(
        st.lists(children, max_size=4),
        st.lists(children, max_size=4).map(tuple),
        st.dictionaries(hashable_keys, children, max_size=4),
    )


literals = st.recursive(scalars, containers, max_leaves=12)


def scalar_of_kind(kind: ValueKind) -> st.SearchStrategy[object]:
    return {
        ValueKind.INT: st.integers(min_value=-(10**12), max_value=10**12),
        ValueKind.FLOAT: finite_floats,
        ValueKind.STR: st.text(max_size=30),
        ValueKind.BOOL: st.booleans(),
        ValueKind.NONE: scalars,
    }[kind]


# ---------------------------------------------------------------------- rendering
@given(literals)
def test_every_literal_reads_back_exactly(value: object) -> None:
    text = render_literal(value)

    assert same_literal(ast.literal_eval(text), value)
    assert "\n" not in text
    text.encode("utf-8")  # always encodable: no lone surrogates leak through


@given(literals, st.sampled_from(['"', "'"]), st.booleans())
def test_rendering_style_never_changes_the_value(value: object, quote: str, group: bool) -> None:
    text = render_literal(value, quote=quote, group_digits=group)

    assert same_literal(ast.literal_eval(text), value)


# ------------------------------------------------------------------ real-file edits
def _statement_lines(constant: Constant) -> range:
    return range(constant.line - 1, constant.end_line)


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(data=st.data())
def test_a_scalar_edit_changes_that_constant_and_nothing_else(data: st.DataObject) -> None:
    name = data.draw(st.sampled_from(sorted(SCALAR_CONSTANTS)))
    newline = data.draw(st.sampled_from(["\n", "\r\n"]))
    source = REAL_SOURCES[name].replace("\n", newline)
    by_name = {c.name: c for c in editor.constants(source)}
    constant = by_name[data.draw(st.sampled_from(SCALAR_CONSTANTS[name]))]
    new_value = data.draw(scalar_of_kind(constant.kind))

    after = editor.edit(source, {constant.name: new_value})

    before_lines = source.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    inside = _statement_lines(constant)
    assert len(before_lines) == len(after_lines)
    for index, (old, new) in enumerate(zip(before_lines, after_lines, strict=True)):
        if index not in inside:
            assert old == new, f"line {index + 1} changed outside {constant.name}"
    edited = {c.name: c for c in editor.constants(after)}
    assert same_literal(edited[constant.name].value, new_value)
    assert after.count("\r\n") == (after.count("\n") if newline == "\r\n" else 0)


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(data=st.data())
def test_a_dict_edit_keeps_every_unchanged_entry_verbatim(data: st.DataObject) -> None:
    source = REAL_SOURCES["10_multi_strategy.py"]
    tickers = next(c for c in editor.constants(source) if c.name == "TICKERS")
    assert isinstance(tickers.value, dict)
    old_keys = list(tickers.value)

    kept = data.draw(st.lists(st.sampled_from(old_keys), unique=True, max_size=len(old_keys)))
    added = data.draw(
        st.dictionaries(st.text(min_size=1, max_size=6), st.text(max_size=8), max_size=3)
    )
    assume(not set(added) & set(old_keys))
    new_value: dict[LiteralValue, LiteralValue] = {**{k: tickers.value[k] for k in kept}, **added}
    assume(bool(new_value))

    after = editor.edit(source, {"TICKERS": new_value})

    assert same_literal(
        next(c for c in editor.constants(after) if c.name == "TICKERS").value, new_value
    )
    other_before = [c.value for c in editor.constants(source) if c.name != "TICKERS"]
    other_after = [c.value for c in editor.constants(after) if c.name != "TICKERS"]
    assert same_literal(other_before, other_after)
    # every kept entry that was not last keeps its exact line: comma, spacing and all
    original = source.splitlines()
    span = original[tickers.line : tickers.end_line - 1]  # the entries, without braces
    for key, line in zip(old_keys, span, strict=True):
        if key in kept and key != old_keys[-1]:
            assert line in after.splitlines()
    # and the file outside the dict is untouched
    assert source.splitlines()[: tickers.line] == after.splitlines()[: tickers.line]
    assert (
        source.splitlines()[tickers.end_line :]
        == after.splitlines()[-(len(original) - tickers.end_line) :]
    )


@settings(max_examples=100, deadline=None)
@given(new_value=st.lists(scalars, min_size=1, max_size=6).map(tuple))
def test_a_tuple_constant_accepts_any_tuple_of_scalars(new_value: tuple[object, ...]) -> None:
    source = REAL_SOURCES["06_options.py"]

    after = editor.edit(source, {"MONEYNESS_RANGE": new_value})

    edited = next(c for c in editor.constants(after) if c.name == "MONEYNESS_RANGE")
    assert same_literal(edited.value, new_value)
