"""The state behind the configuration form: what the user typed and what it means (Qt-free).

Every editable constant becomes a *field* with the text shown to the user. Typing changes
the text; the field is *dirty* when the text parses to a value different from the file's, and
*invalid* when it does not parse as the constant's type. Which widget shows a field, and how,
is the view's business; deciding what an edit means is not, so it lives here where it can be
tested without a display.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quant_workbench.application.config import ProjectConstant
from quant_workbench.domain.config import Constant
from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.domain.literals import LiteralValue, ValueKind, parse_text, same_literal

#: Values whose source is longer than this (or spans lines) get a multi-line editor.
LONG_VALUE = 60


def display_text(constant: Constant) -> str:
    """What to show in the editor for a constant's current value.

    Strings are shown without quotes (the type is fixed, so quoting is noise), booleans as
    ``true``/``false``, everything else exactly as written in the file (``10_000.0``, a
    multi-line dict) with Unix newlines.
    """
    if constant.kind is ValueKind.STR:
        return str(constant.value)
    if constant.kind is ValueKind.BOOL:
        return "true" if constant.value else "false"
    return constant.source.replace("\r\n", "\n")


def wants_multiline_editor(constant: Constant) -> bool:
    return constant.kind.is_container and (
        constant.is_multiline or len(constant.source) > LONG_VALUE
    )


@dataclass(slots=True)
class Field:
    """One constant in the form."""

    entry: ProjectConstant
    original: str
    text: str
    #: Why ``text`` is not a valid value, or ``None``.
    error: str | None = None
    #: The parsed value when the text is valid.
    value: LiteralValue | None = None

    @property
    def key(self) -> str:
        return self.entry.qualified_name

    @property
    def constant(self) -> Constant:
        return self.entry.constant

    @property
    def is_dirty(self) -> bool:
        return self.error is None and not same_literal(self.value, self.constant.value)


class ConfigFormState:
    """All the fields of one project's configuration, and what saving them would change."""

    def __init__(self, entries: Sequence[ProjectConstant]) -> None:
        self._fields: dict[str, Field] = {}
        for entry in entries:
            text = display_text(entry.constant)
            self._fields[entry.qualified_name] = Field(
                entry, original=text, text=text, value=entry.constant.value
            )

    @property
    def fields(self) -> tuple[Field, ...]:
        return tuple(self._fields.values())

    def field(self, key: str) -> Field:
        return self._fields[key]

    def set_text(self, key: str, text: str) -> Field:
        """Record what the user typed and re-validate it."""
        field = self._fields[key]
        field.text = text
        try:
            field.value = parse_text(field.constant.kind, text, name=field.constant.name)
            field.error = None
        except UnsafeEditError as error:
            field.value = None
            field.error = error.message
        return field

    def reset(self) -> None:
        """Discard every edit."""
        for key in self._fields:
            self.set_text(key, self._fields[key].original)

    @property
    def dirty_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self._fields.values() if f.is_dirty)

    @property
    def invalid_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self._fields.values() if f.error is not None)

    @property
    def can_save(self) -> bool:
        """There is something to save and nothing that cannot be saved."""
        return bool(self.dirty_keys) and not self.invalid_keys

    def changes(self) -> dict[str, LiteralValue]:
        """The values to write, keyed ``file:NAME``, for every dirty field."""
        return {f.key: f.value for f in self._fields.values() if f.is_dirty}
