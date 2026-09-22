"""``ui/app.py``: process-level start-up glue not covered by the main-window tests."""

from __future__ import annotations

import pytest

from quant_workbench.ui.app import _set_windows_taskbar_identity

pytestmark = pytest.mark.gui


def test_setting_the_windows_taskbar_identity_never_raises() -> None:
    """A no-op off Windows; on Windows, tells the shell not to fall back to the exe's own icon.

    Without this, Windows groups the taskbar button (and picks its icon) by the launching
    .exe rather than the window's own icon — for the gui-script pip/hatchling generates,
    that is a generic "Python script" icon, not this app's.
    """
    _set_windows_taskbar_identity()  # must not raise on any platform
