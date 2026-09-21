"""Use cases around a project's configuration constants: list, preview, apply.

Editing is always two steps. :meth:`ConfigService.plan` computes the new file contents and a
unified diff *without touching the disk*; :meth:`ConfigService.apply` writes a plan. Splitting
them is what lets the CLI print a diff first and the GUI show a preview dialog, both through
the same code path.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quant_workbench.domain.config import Constant
from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.literals import LiteralValue, parse_text
from quant_workbench.domain.ports import ConfigEditor, ProjectFiles
from quant_workbench.domain.project import Project


@dataclass(frozen=True, slots=True)
class ProjectConstant:
    """A constant together with the project file that defines it."""

    file: str
    constant: Constant

    @property
    def qualified_name(self) -> str:
        return f"{self.file}:{self.constant.name}"


@dataclass(frozen=True, slots=True)
class FileChange:
    """The planned rewrite of one file."""

    file: str
    before: str
    after: str
    names: tuple[str, ...]

    def inverse(self) -> FileChange:
        """The change that undoes this one (used for undo)."""
        return FileChange(self.file, self.after, self.before, self.names)

    @property
    def diff(self) -> str:
        """Unified diff, in the ``a/`` / ``b/`` form that ``git apply`` also understands."""
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.file}",
                tofile=f"b/{self.file}",
            )
        )


@dataclass(frozen=True, slots=True)
class ConfigPlan:
    """What applying a set of changes would do; empty when they change nothing."""

    project: Slug
    changes: tuple[FileChange, ...]

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def inverse(self) -> ConfigPlan:
        """The plan that restores the files this one changes (applies only if nothing moved)."""
        return ConfigPlan(self.project, tuple(change.inverse() for change in self.changes))

    @property
    def diff(self) -> str:
        return "".join(change.diff for change in self.changes)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    files: tuple[str, ...]
    backups: tuple[Path, ...]


class ConfigService:
    def __init__(self, *, files: ProjectFiles, editor: ConfigEditor) -> None:
        self._files = files
        self._editor = editor

    # ---------------------------------------------------------------------- reading
    def constants(self, project: Project) -> tuple[ProjectConstant, ...]:
        """Every editable constant of the files the project declares as configuration."""
        found: list[ProjectConstant] = []
        for target in project.spec.config_targets:
            text = self._files.read_text(project, target.file)
            if text is None:
                continue
            found.extend(
                ProjectConstant(target.file, constant)
                for constant in self._editor.constants(text, symbols=target.symbols)
            )
        return tuple(found)

    def parse_assignments(
        self, project: Project, assignments: Mapping[str, str]
    ) -> dict[str, LiteralValue]:
        """Turn command-line text (``"0.05"``) into typed values, using each constant's type."""
        constants = self.constants(project)
        return {
            key: parse_text(self._find(constants, key).constant.kind, text, name=key)
            for key, text in assignments.items()
        }

    # --------------------------------------------------------------------- planning
    def plan(self, project: Project, changes: Mapping[str, object]) -> ConfigPlan:
        """Compute the effect of setting constants (keys are ``NAME`` or ``file:NAME``)."""
        constants = self.constants(project)
        per_file: dict[str, dict[str, object]] = defaultdict(dict)
        for key, value in changes.items():
            target = self._find(constants, key)
            per_file[target.file][target.constant.name] = value

        planned: list[FileChange] = []
        for file, requested in per_file.items():
            before = self._files.read_text(project, file)
            if before is None:  # pragma: no cover - constants() just read it
                raise UnsafeEditError(f"{file} disappeared while planning the edit")
            _refuse_undecodable(file, before)
            after = self._editor.edit(before, requested)
            if after != before:
                planned.append(FileChange(file, before, after, tuple(requested)))
        return ConfigPlan(project.slug, tuple(planned))

    # ---------------------------------------------------------------------- writing
    def apply(self, project: Project, plan: ConfigPlan) -> ApplyResult:
        """Write a plan to disk, keeping a backup of every file it replaces.

        Nothing is written if any file changed since the plan was made (the user edited it in
        an editor in the meantime): the plan would silently discard that edit.
        """
        for change in plan.changes:
            if self._files.read_text(project, change.file) != change.before:
                raise UnsafeEditError(
                    f"{change.file} changed on disk after the edit was planned",
                    hint="Review the file and plan the edit again.",
                )
        backups: list[Path] = []
        for change in plan.changes:
            backup = self._files.write_text(project, change.file, change.after)
            if backup is not None:
                backups.append(backup)
        return ApplyResult(tuple(c.file for c in plan.changes), tuple(backups))

    def set(self, project: Project, changes: Mapping[str, object]) -> ApplyResult:
        """Plan and apply in one go (no preview)."""
        return self.apply(project, self.plan(project, changes))

    # --------------------------------------------------------------------- internal
    @staticmethod
    def _find(constants: tuple[ProjectConstant, ...], key: str) -> ProjectConstant:
        file, _, name = key.rpartition(":")
        matches = [
            c
            for c in constants
            if c.constant.name == name and (not file or c.file == file.replace("\\", "/"))
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            options = ", ".join(c.qualified_name for c in matches)
            raise UnsafeEditError(
                f"{name} exists in several files", hint=f"Say which one: {options}"
            )
        close = difflib.get_close_matches(name, [c.constant.name for c in constants], n=3)
        hint = f"Did you mean: {', '.join(close)}?" if close else "See `qw config get <project>`."
        raise UnsafeEditError(f"No editable constant named {key}", hint=hint)


def _refuse_undecodable(file: str, text: str) -> None:
    """``read_text`` decodes with replacement characters; writing those back would corrupt."""
    if "�" in text:
        raise UnsafeEditError(
            f"{file} is not valid UTF-8, so it cannot be rewritten safely",
            hint="Convert the file to UTF-8 first.",
        )
