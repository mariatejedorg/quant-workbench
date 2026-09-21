"""Identifier value objects.

Identifiers are plain ``str`` at runtime (cheap, JSON/TOML/SQL friendly) but distinct
types for the type checker, so a run id can never be passed where a project slug is
expected.
"""

from __future__ import annotations

import re
import secrets
from typing import TYPE_CHECKING, NewType

from quant_workbench.domain.errors import InvalidSlugError

if TYPE_CHECKING:
    # ``ids`` is a leaf module that ``ports`` (through ``project``) depends on, so the
    # Clock protocol is imported for the type checker only, to avoid an import cycle.
    from quant_workbench.domain.ports import Clock

Slug = NewType("Slug", str)
RunId = NewType("RunId", str)

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SLUG_LENGTH = 64


def parse_slug(value: str) -> Slug:
    """Validate ``value`` as a kebab-case project slug and return it as a :class:`Slug`.

    >>> parse_slug("credit-risk-merton-model")
    'credit-risk-merton-model'
    """
    if not value or len(value) > _MAX_SLUG_LENGTH or not _SLUG_RE.fullmatch(value):
        raise InvalidSlugError(
            f"{value!r} is not a valid project slug",
            hint="Use lowercase letters, digits and single hyphens, up to 64 characters.",
        )
    return Slug(value)


def new_run_id(clock: Clock) -> RunId:
    """Create a run id that sorts chronologically and is unique across processes.

    Layout: 13 hex digits of the millisecond timestamp, a dash, 8 hex digits of
    randomness. Lexicographic order equals creation order (until the year 10889), which
    keeps ``ORDER BY id`` meaningful in SQLite without an extra column.
    """
    millis = int(clock.now().timestamp() * 1000)
    return RunId(f"{millis:013x}-{secrets.token_hex(4)}")
