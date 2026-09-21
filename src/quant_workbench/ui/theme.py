"""Colours and the Qt style sheet, derived from the portfolio's own dashboard palette.

The ten dashboards share a palette (blue accent, warm greys, red/orange for warnings). The
desktop app reuses it so the workbench and the things it opens look like one product. Tokens
are plain dicts so tests can check contrast without a display; :func:`build_stylesheet` turns
them into QSS.
"""

from __future__ import annotations

from dataclasses import dataclass

from quant_workbench.domain.diagnostics import Severity
from quant_workbench.domain.runs import RunStatus


@dataclass(frozen=True, slots=True)
class Tokens:
    """One theme's colours."""

    name: str
    window: str  # the app background
    surface: str  # panels, inputs, tables
    ink: str  # primary text
    ink_secondary: str
    ink_muted: str
    grid: str  # hairlines between rows
    baseline: str  # borders
    accent: str
    success: str
    warning: str
    error: str
    selection: str  # selected row background
    console_bg: str
    console_fg: str


LIGHT = Tokens(
    name="light",
    window="#f9f9f7",
    surface="#fcfcfb",
    ink="#0b0b0b",
    ink_secondary="#52514e",
    ink_muted="#6f6e69",
    grid="#e1e0d9",
    baseline="#c3c2b7",
    accent="#2a78d6",
    success="#2b8a5e",
    warning="#b7791f",
    error="#d13b3a",
    selection="#dbe8f8",
    console_bg="#ffffff",
    console_fg="#1b1b1a",
)

DARK = Tokens(
    name="dark",
    window="#161615",
    surface="#1e1e1c",
    ink="#f2f1ec",
    ink_secondary="#b4b3ad",
    ink_muted="#94938c",
    grid="#33322f",
    baseline="#4a4945",
    accent="#5a9bea",
    success="#45b784",
    warning="#e8b45c",
    error="#ef6a69",
    selection="#26384f",
    console_bg="#121211",
    console_fg="#e6e5df",
)

THEMES = {"light": LIGHT, "dark": DARK}


def tokens_for(name: str, *, system_is_dark: bool = False) -> Tokens:
    """The tokens of ``"light"``, ``"dark"`` or ``"system"`` (which follows the OS)."""
    if name == "system":
        return DARK if system_is_dark else LIGHT
    return THEMES[name]


def status_colour(status: RunStatus | None, tokens: Tokens) -> str:
    """The colour that stands for a run status (grey when the project never ran)."""
    if status is None:
        return tokens.ink_muted
    if status is RunStatus.SUCCEEDED:
        return tokens.success
    if status in (RunStatus.FAILED, RunStatus.TIMED_OUT):
        return tokens.error
    if status in (RunStatus.RUNNING, RunStatus.QUEUED):
        return tokens.accent
    return tokens.warning  # cancelled, skipped


def severity_colour(severity: Severity, tokens: Tokens) -> str:
    return {
        Severity.ERROR: tokens.error,
        Severity.WARNING: tokens.warning,
        Severity.INFO: tokens.ink_muted,
    }[severity]


#: sRGB channel value below which the transfer curve is linear (WCAG relative luminance).
_LINEAR_THRESHOLD = 0.03928


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio of two ``#rrggbb`` colours (4.5 is the AA threshold for text)."""

    def luminance(colour: str) -> float:
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [
            c / 12.92 if c <= _LINEAR_THRESHOLD else ((c + 0.055) / 1.055) ** 2.4 for c in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def build_stylesheet(t: Tokens) -> str:
    """The application's QSS for a theme."""
    return f"""
QWidget {{ color: {t.ink}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QToolTip {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.baseline}; padding: 4px; }}

QMenuBar {{ background: {t.window}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t.selection}; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.baseline}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: 3px; }}
QMenu::item:disabled {{ color: {t.ink_muted}; }}
QMenu::separator {{ height: 1px; background: {t.grid}; margin: 4px 6px; }}

QToolBar {{ background: {t.window}; border: none; border-bottom: 1px solid {t.grid}; spacing: 6px; padding: 4px; }}
QToolButton {{ padding: 5px 10px; border: 1px solid transparent; border-radius: 4px; }}
QToolButton:hover {{ background: {t.selection}; }}
QToolButton:disabled {{ color: {t.ink_muted}; }}
QToolButton:checked {{ background: {t.selection}; border-color: {t.accent}; }}

QDockWidget {{ titlebar-close-icon: none; font-weight: 600; }}
QDockWidget::title {{ background: {t.window}; padding: 6px 8px; border-bottom: 1px solid {t.grid}; }}

QListView, QTableView, QTreeView, QTextBrowser, QPlainTextEdit, QLineEdit, QSpinBox, QComboBox {{
    background: {t.surface}; border: 1px solid {t.baseline}; border-radius: 4px;
    selection-background-color: {t.selection}; selection-color: {t.ink};
}}
QLineEdit, QSpinBox, QComboBox {{ padding: 4px 6px; min-height: 20px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"] {{ border: 1px solid {t.error}; }}
QListView::item {{ padding: 5px 6px; }}
QListView::item:selected, QTableView::item:selected {{ background: {t.selection}; color: {t.ink}; }}
QTableView {{ gridline-color: {t.grid}; alternate-background-color: {t.window}; }}
QHeaderView::section {{ background: {t.window}; color: {t.ink_secondary}; padding: 5px 8px; border: none;
    border-bottom: 1px solid {t.baseline}; font-weight: 600; }}

QPlainTextEdit#console {{ background: {t.console_bg}; color: {t.console_fg};
    font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }}

QTabWidget::pane {{ border: 1px solid {t.baseline}; border-radius: 4px; background: {t.surface}; top: -1px; }}
QTabBar::tab {{ padding: 7px 14px; border: 1px solid transparent; color: {t.ink_secondary}; }}
QTabBar::tab:selected {{ color: {t.ink}; border-bottom: 2px solid {t.accent}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ color: {t.ink}; }}

QPushButton {{ background: {t.surface}; border: 1px solid {t.baseline}; border-radius: 4px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:default {{ background: {t.accent}; border-color: {t.accent}; color: #ffffff; }}
QPushButton:disabled {{ color: {t.ink_muted}; }}
QPushButton:default:disabled {{ background: {t.surface}; border-color: {t.baseline}; color: {t.ink_muted}; }}
QScrollArea {{ background: {t.surface}; border: 1px solid {t.baseline}; border-radius: 4px; }}
#form-host {{ background: {t.surface}; }}

QStatusBar {{ background: {t.window}; border-top: 1px solid {t.grid}; color: {t.ink_secondary}; }}
QProgressBar {{ border: 1px solid {t.baseline}; border-radius: 3px; background: {t.surface}; text-align: center;
    max-height: 14px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 2px; }}
QSplitter::handle {{ background: {t.grid}; }}
"""  # noqa: E501 - QSS reads best one rule per line
