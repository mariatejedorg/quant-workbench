"""Builders for synthetic workspaces used across the test-suite."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def write_project(
    workspace: Path,
    folder: str,
    *,
    refs: Iterable[str] = (),
    docstring_mention: str | None = None,
    manifest: str | None = None,
    readme: str | None = None,
    src_main: bool = True,
    requirements: bool = True,
    dashboard: bool = True,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Create a minimal project folder and return its path.

    ``refs`` become string literals in ``src/loader.py`` (how real projects reference
    their siblings); ``docstring_mention`` puts a folder name inside prose instead, which
    must *not* be detected as a dependency.
    """
    root = workspace / folder
    (root / "src").mkdir(parents=True)
    if src_main:
        (root / "src" / "main.py").write_text('print("hello")\n', encoding="utf-8")
    if requirements:
        (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    if dashboard:
        (root / "outputs").mkdir()
        (root / "outputs" / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    lines = ["from pathlib import Path", ""]
    if docstring_mention:
        lines.insert(
            0, f'"""Loads code from {docstring_mention}/src/x.py (prose, not a reference)."""'
        )
    lines += [f'REF_{i} = Path(__file__).parents[2] / "{ref}"' for i, ref in enumerate(refs)]
    (root / "src" / "loader.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if readme is not None:
        (root / "README.md").write_text(readme, encoding="utf-8")
    if manifest is not None:
        (root / "quant-project.toml").write_text(manifest, encoding="utf-8")
    for relative, content in (extra_files or {}).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def manifest_toml(
    slug: str, *, folder: str | None = None, deps: Iterable[str] = (), title: str | None = None
) -> str:
    """A small valid manifest document."""
    dependencies = ", ".join(f'"{d}"' for d in deps)
    lines = [
        f'slug = "{slug}"',
        f'title = "{title or slug.title()}"',
        'category = "Test"',
        f"depends_on = [{dependencies}]",
    ]
    if folder:
        lines.insert(1, f'folder = "{folder}"')
    return "\n".join(lines) + "\n"
