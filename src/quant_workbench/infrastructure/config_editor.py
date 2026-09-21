"""Format-preserving editor for module-level constants, built on libcst.

Why libcst and not ``ast`` or regular expressions? ``ast`` throws away comments and
whitespace, so writing a value back would reformat the whole file (and lose the very
comments that document each parameter). A regular expression cannot tell ``TICKER = "A"``
from the same text inside a string or a dict. libcst keeps a *concrete* syntax tree: every
space, comment and line ending is a node, so we can replace one value node and print the
tree back byte for byte everywhere else.

The safety net: after every edit the result is re-read and compared with what was asked for,
and every *other* constant must have kept its value. If anything disagrees the edit is
refused (:class:`~quant_workbench.domain.errors.UnsafeEditError`) and nothing is written.
"""

from __future__ import annotations

import ast
import difflib
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from quant_workbench.domain.config import Constant
from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.domain.literals import (
    LiteralValue,
    coerce_for,
    is_editable_literal,
    kind_of,
    render_literal,
    same_literal,
)

_Element = TypeVar("_Element", cst.Element, cst.DictElement)


class _NotALiteral(Exception):
    """The expression is not a plain literal (a call, an arithmetic expression, ...)."""


class _Unmergeable(Exception):
    """A container cannot be edited element by element; replace it as a whole instead."""


@dataclass(frozen=True, slots=True)
class _Binding:
    """One ``NAME = value`` statement at module level."""

    statement: cst.SimpleStatementLine
    name: str
    value: cst.BaseExpression
    #: 0 for the first statement of the module, which also owns the file header comments.
    index: int


def _binding_of(statement: cst.CSTNode, index: int) -> _Binding | None:
    """``NAME = value`` / ``NAME: type = value`` on a line of its own, else ``None``.

    Tuple unpacking, chained assignments (``A = B = 1``), attribute targets and lines with
    several statements (``A = 1; B = 2``) are deliberately not editable.
    """
    if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
        return None
    small = statement.body[0]
    if isinstance(small, cst.Assign) and len(small.targets) == 1:
        target = small.targets[0].target
        value: cst.BaseExpression | None = small.value
    elif isinstance(small, cst.AnnAssign):
        target, value = small.target, small.value
    else:
        return None
    if not isinstance(target, cst.Name) or value is None:
        return None
    return _Binding(statement, target.value, value, index)


def _parse(source: str) -> cst.Module:
    try:
        return cst.parse_module(source)
    except cst.ParserSyntaxError as error:
        raise UnsafeEditError(
            f"The file has a syntax error and cannot be edited safely: {error.message}",
            hint="Fix the syntax error by hand first.",
        ) from error


class _Values:
    """Evaluate literal nodes and build replacement nodes for them."""

    def __init__(self, module: cst.Module) -> None:
        self._module = module

    def evaluate(self, node: cst.BaseExpression) -> LiteralValue:
        """The Python value of a literal node; raises :class:`_NotALiteral` otherwise."""
        code = self._module.code_for_node(node)
        try:
            value = ast.literal_eval(code)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as error:
            raise _NotALiteral(code) from error
        if not is_editable_literal(value):
            raise _NotALiteral(code)
        return value  # type: ignore[no-any-return]  # is_editable_literal proved the shape

    def source_of(self, node: cst.BaseExpression) -> str:
        return self._module.code_for_node(node)

    # ------------------------------------------------------------------ rewriting
    def rewrite(self, old: cst.BaseExpression, new: LiteralValue) -> cst.BaseExpression:
        """A node for ``new`` that reuses as much of ``old`` (and its formatting) as possible.

        Containers are edited element by element so untouched entries keep their comments
        and layout; anything else (or a container that cannot be merged) is re-rendered.
        """
        try:
            if isinstance(old, cst.Dict) and isinstance(new, dict) and old.elements and new:
                return self._merge_dict(old, new)
            if isinstance(old, cst.List) and isinstance(new, list) and old.elements and new:
                return old.with_changes(elements=self._merge_sequence(old, new))
            if isinstance(old, cst.Tuple) and isinstance(new, tuple) and old.elements and new:
                return old.with_changes(elements=self._merge_sequence(old, new))
        except _Unmergeable:
            pass
        return self._replace(old, new)

    def _replace(self, old: cst.BaseExpression, new: LiteralValue) -> cst.BaseExpression:
        node = self._expression(new, self._style(old))
        if kind_of(new) is not None and not isinstance(new, tuple | list | dict):
            # a scalar keeps the parentheses it had: ``X = (0.5)`` stays ``X = (0.7)``
            node = node.with_changes(lpar=old.lpar, rpar=old.rpar)
        return node

    def _merge_dict(self, old: cst.Dict, new: dict[LiteralValue, LiteralValue]) -> cst.Dict:
        olds = [element for element in old.elements if isinstance(element, cst.DictElement)]
        if len(olds) != len(old.elements):
            raise _Unmergeable  # pragma: no cover - ``**other`` is not a literal, never listed
        # ``repr`` keeps 1, 1.0 and True apart, which plain ``==`` on the keys would not
        index_by_key = {repr(self.evaluate(element.key)): i for i, element in enumerate(olds)}
        if len(index_by_key) != len(olds):
            raise _Unmergeable  # duplicate keys
        style = self._style(old)
        template = olds[-1]
        merged: list[tuple[cst.DictElement, int | None]] = []
        for key, value in new.items():
            position = index_by_key.get(repr(key))
            if position is None:
                fresh = template.with_changes(
                    key=self._expression(key, style), value=self._expression(value, style)
                )
                merged.append((fresh, None))
                continue
            element = olds[position]
            if not same_literal(self.evaluate(element.value), value):
                element = element.with_changes(value=self.rewrite(element.value, value))
            merged.append((element, position))
        return old.with_changes(elements=_reflow_commas(olds, merged))

    def _merge_sequence(
        self, old: cst.List | cst.Tuple, new: Sequence[LiteralValue]
    ) -> list[cst.Element]:
        olds = [element for element in old.elements if isinstance(element, cst.Element)]
        if len(olds) != len(old.elements):
            raise _Unmergeable  # pragma: no cover - ``*other`` is not a literal, never listed
        style = self._style(old)
        template = olds[-1]
        merged: list[tuple[cst.Element, int | None]] = []
        for position, value in enumerate(new):
            if position < len(olds):
                element = olds[position]
                if not same_literal(self.evaluate(element.value), value):
                    element = element.with_changes(value=self.rewrite(element.value, value))
                merged.append((element, position))
            else:
                fresh = template.with_changes(value=self._expression(value, style))
                merged.append((fresh, None))
        elements = _reflow_commas(olds, merged)
        if isinstance(old, cst.Tuple) and len(olds) == 1 and len(elements) > 1:
            # ``(1,)`` needs its comma to be a tuple; ``(1, 2,)`` would only be untidy
            elements[-1] = elements[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return elements

    # -------------------------------------------------------------------- helpers
    def _style(self, node: cst.BaseExpression) -> _Style:
        text = self.source_of(node)
        quotes = [(text.find(q), q) for q in ('"', "'") if q in text]
        return _Style(
            quote=min(quotes)[1] if quotes else '"',
            group_digits=re.search(r"\d_\d", text) is not None,
        )

    @staticmethod
    def _expression(value: LiteralValue, style: _Style) -> cst.BaseExpression:
        return cst.parse_expression(
            render_literal(value, quote=style.quote, group_digits=style.group_digits)
        )


@dataclass(frozen=True, slots=True)
class _Style:
    """The conventions of the value being replaced, so the new one looks like it belongs."""

    quote: str
    group_digits: bool


def _without_comment(comma: cst.Comma | cst.MaybeSentinel) -> cst.Comma | cst.MaybeSentinel:
    """``comma`` minus any trailing ``# comment`` that lives in its whitespace.

    A comment written after an entry belongs to that entry; when the comma is reused for a
    *different* one it must not be copied along.
    """
    if not isinstance(comma, cst.Comma):
        return comma
    after = comma.whitespace_after
    if isinstance(after, cst.ParenthesizedWhitespace):
        first_line = after.first_line.with_changes(
            comment=None, whitespace=cst.SimpleWhitespace("")
        )
        after = after.with_changes(first_line=first_line, empty_lines=[])
    return comma.with_changes(whitespace_after=after)


def _reflow_commas(
    olds: Sequence[_Element], merged: Sequence[tuple[_Element, int | None]]
) -> list[_Element]:
    """Give every element of the edited container the right comma.

    Each element remembers the comma it had (which carries its trailing comment and the
    layout of the following line). Only an element whose *position class* changes needs a
    different one: what was last but now is not (a middle comma, as the other entries have),
    and what is last now (the original last comma, whether or not it had a trailing comma).
    """
    middle_source = next((e.comma for e in olds[:-1] if isinstance(e.comma, cst.Comma)), None)
    middle = (
        _without_comment(middle_source)
        if middle_source is not None
        else cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
    )
    tail = _without_comment(olds[-1].comma)
    was_last = len(olds) - 1
    last = len(merged) - 1
    result: list[_Element] = []
    for position, (element, origin) in enumerate(merged):
        if position == last:
            comma = element.comma if origin == was_last else tail
        elif origin is None or origin == was_last:
            comma = middle
        else:
            comma = element.comma
        result.append(element.with_changes(comma=comma))
    return result


class _Rewriter(cst.CSTTransformer):
    """Replaces the value of the requested module-level constants.

    Two ``visit_`` methods return ``False`` so the transformer never descends into an
    indented block (function, class, ``if``...) or a one-line suite: only statements
    directly in the module body are candidates.
    """

    def __init__(self, values: _Values, changes: Mapping[str, LiteralValue]) -> None:
        super().__init__()
        self._values = values
        self._changes = changes
        self.applied: set[str] = set()

    def visit_IndentedBlock(self, node: cst.IndentedBlock) -> bool:
        return False

    def visit_SimpleStatementSuite(self, node: cst.SimpleStatementSuite) -> bool:
        return False

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine:
        binding = _binding_of(original_node, 0)
        if binding is None or binding.name not in self._changes:
            return updated_node
        new_value = self._values.rewrite(binding.value, self._changes[binding.name])
        self.applied.add(binding.name)
        (small,) = updated_node.body
        return updated_node.with_changes(body=[small.with_changes(value=new_value)])


class LibCstConfigEditor:
    """Implements :class:`~quant_workbench.domain.ports.ConfigEditor`."""

    # ---------------------------------------------------------------------- reading
    def constants(self, source: str, *, symbols: Collection[str] = ()) -> tuple[Constant, ...]:
        wrapper = MetadataWrapper(_parse(source))
        module = wrapper.module
        positions = wrapper.resolve(PositionProvider)
        values = _Values(module)
        bindings = [
            binding
            for index, statement in enumerate(module.body)
            if (binding := _binding_of(statement, index)) is not None
        ]
        occurrences = Counter(binding.name for binding in bindings)
        found: list[Constant] = []
        for binding in bindings:
            if occurrences[binding.name] > 1 or (symbols and binding.name not in symbols):
                continue  # assigned twice: which value wins depends on control flow
            try:
                value = values.evaluate(binding.value)
            except _NotALiteral:
                continue
            kind = kind_of(value)
            if kind is None:  # pragma: no cover - evaluate() only returns known kinds
                continue
            span = positions[binding.statement]
            found.append(
                Constant(
                    name=binding.name,
                    kind=kind,
                    value=value,
                    source=values.source_of(binding.value),
                    description=_describe(module, binding),
                    line=span.start.line,
                    end_line=span.end.line,
                )
            )
        return tuple(found)

    # --------------------------------------------------------------------- editing
    def edit(self, source: str, changes: Mapping[str, object]) -> str:
        if not changes:
            return source
        editable = {constant.name: constant for constant in self.constants(source)}
        coerced: dict[str, LiteralValue] = {}
        for name, value in changes.items():
            constant = editable.get(name)
            if constant is None:
                raise _unknown_constant(name, editable, source)
            coerced[name] = coerce_for(constant.kind, value, name=name)

        # Setting a constant to the value it already has must not touch the file, even to
        # re-spell it: ``0.90`` -> ``0.9`` would be a diff that means nothing.
        coerced = {n: v for n, v in coerced.items() if not same_literal(editable[n].value, v)}
        if not coerced:
            return source

        module = _parse(source)
        rewriter = _Rewriter(_Values(module), coerced)
        edited = module.visit(rewriter).code
        if rewriter.applied != set(coerced):  # pragma: no cover - guarded by the checks above
            raise UnsafeEditError("Could not locate every requested constant in the syntax tree")
        self._verify(edited, editable, coerced)
        return edited

    def _verify(
        self, edited: str, before: Mapping[str, Constant], coerced: Mapping[str, LiteralValue]
    ) -> None:
        """Re-read ``edited`` and prove that only the requested constants changed."""
        after = {constant.name: constant for constant in self.constants(edited)}
        for name, constant in before.items():
            expected = coerced.get(name, constant.value)
            if name not in after or not same_literal(after[name].value, expected):
                raise UnsafeEditError(
                    f"Refusing to write: {name} would not read back as requested",
                    hint="Edit this constant by hand.",
                )
        if after.keys() != before.keys():
            raise UnsafeEditError(  # pragma: no cover - a defensive invariant
                "Refusing to write: the set of constants changed unexpectedly"
            )


def _describe(module: cst.Module, binding: _Binding) -> str:
    """The comment block directly above the statement plus the one at the end of its line.

    A blank line ends the block, so a section heading separated by an empty line is not
    attributed to the constant below it. The first statement of a file also owns the header.
    """
    statement = binding.statement
    lines = statement.leading_lines
    if binding.index == 0:
        lines = (*module.header, *lines)
    block: list[str] = []
    for line in reversed(lines):
        if line.comment is None:
            break
        block.append(line.comment.value.lstrip("#").strip())
    parts = [" ".join(reversed(block))]
    trailing = statement.trailing_whitespace.comment
    if trailing is not None:
        parts.append(trailing.value.lstrip("#").strip())
    return " ".join(part for part in parts if part)


def _unknown_constant(name: str, editable: Mapping[str, Constant], source: str) -> UnsafeEditError:
    module = _parse(source)
    assigned = Counter(
        binding.name
        for index, statement in enumerate(module.body)
        if (binding := _binding_of(statement, index)) is not None
    )
    if assigned[name] > 1:
        return UnsafeEditError(
            f"{name} is assigned more than once in the file, so which value counts is ambiguous",
            hint="Edit this constant by hand.",
        )
    if name in assigned:
        return UnsafeEditError(
            f"{name} is not a plain literal (it is computed), so it cannot be edited safely",
            hint="Edit this constant by hand.",
        )
    close = difflib.get_close_matches(name, editable, n=3, cutoff=0.6)
    hint = f"Did you mean: {', '.join(close)}?" if close else f"Editable: {', '.join(editable)}"
    return UnsafeEditError(f"No editable constant named {name}", hint=hint)
