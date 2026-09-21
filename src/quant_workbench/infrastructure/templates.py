"""The project template shipped with the workbench, rendered with Jinja2."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

#: Template file -> path in the new project (``{module}`` is replaced by the config module name).
#: Files ending in ``.j2`` are rendered; the others are copied as they are (they contain
#: braces that Jinja would mistake for its own syntax, and nothing to substitute).
_LAYOUT: dict[str, str] = {
    "quant-project.toml.j2": "quant-project.toml",
    "README.md.j2": "README.md",
    "requirements.txt.j2": "requirements.txt",
    "gitignore.j2": ".gitignore",
    "config.py.j2": "config/{module}.py",
    "main.py.j2": "src/main.py",
    "simulate.py": "src/simulate.py",
    "dashboard.py": "src/dashboard.py",
}


class JinjaProjectTemplates:
    """Implements :class:`~quant_workbench.domain.ports.ProjectTemplates`."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        # StrictUndefined: a variable the template needs but the context lacks is an error,
        # not silently an empty string in a generated file.
        self._environment = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,  # noqa: S701 - the output is source code, not HTML
        )

    def render(self, context: Mapping[str, str | int]) -> dict[str, str]:
        files: dict[str, str] = {}
        for template, destination in _LAYOUT.items():
            target = destination.format(module=context["module"])
            if template.endswith(".j2"):
                files[target] = self._environment.get_template(template).render(**context)
            else:
                files[target] = (self._directory / template).read_text(encoding="utf-8")
        files["outputs/.gitkeep"] = ""
        return files
