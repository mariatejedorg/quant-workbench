"""Locating the data files shipped inside the package (registry, signatures, templates)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def data_path(*parts: str) -> Path:
    """Filesystem path of a packaged data file or directory, e.g. ``data_path("registry")``.

    Wheels and editable installs both unpack to real directories, which is all this
    supports (zipped imports are not a supported way to run the workbench).
    """
    return Path(str(files("quant_workbench").joinpath("data", *parts)))
