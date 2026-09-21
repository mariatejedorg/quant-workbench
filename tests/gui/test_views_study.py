"""The Study tab: a session over a project's README, persisted between windows."""

from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.domain.study import Grade
from quant_workbench.ui.main_window import MainWindow

pytestmark = pytest.mark.gui

README = """# Alpha

## Concepts to be able to explain in an interview

- **Volatility clustering**: information arrives in bursts.
- **Alpha and beta**: reaction and memory.
"""


@pytest.fixture
def opened(qtbot, window: MainWindow, workspace: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    (workspace / "alpha" / "README.md").write_text(README, encoding="utf-8")
    assert window.open_workspace(workspace)
    window.select_project("alpha")
    window.views.show("study")
    return window


def test_the_card_is_shown_without_its_answer_until_revealed(opened: MainWindow) -> None:
    study = opened.views.study

    assert "Explain: Volatility clustering" in study.card.toPlainText()
    assert "information arrives" not in study.card.toPlainText()
    assert study.reveal_button.isEnabled()
    assert not any(b.isEnabled() for b in study.grade_buttons.values())
    assert "2 card(s) left today" in study.progress.text()

    study.reveal_button.click()

    assert "information arrives in bursts" in study.card.toPlainText()
    assert not study.reveal_button.isEnabled()
    assert all(b.isEnabled() for b in study.grade_buttons.values())


def test_grading_moves_on_and_finishing_the_queue_says_so(opened: MainWindow) -> None:
    study = opened.views.study

    for _ in range(2):
        study.reveal_button.click()
        study.grade_buttons[Grade.GOOD].click()

    assert "Nothing due today" in study.card.toPlainText()
    assert "2 card(s), 2 learned" in study.progress.text()
    assert not study.reveal_button.isEnabled()


def test_a_grade_needs_the_answer_to_be_revealed_first(opened: MainWindow) -> None:
    study = opened.views.study

    study.grade(Grade.EASY)  # ignored: the answer was not looked at

    assert "2 card(s) left today" in study.progress.text()


def test_progress_is_kept_when_the_project_is_selected_again(opened: MainWindow) -> None:
    study = opened.views.study
    study.reveal_button.click()
    study.grade_buttons[Grade.GOOD].click()

    opened.select_project("beta")
    opened.select_project("alpha")

    assert "1 card(s) left today" in opened.views.study.progress.text()


def test_a_readme_without_cards_and_no_selection(opened: MainWindow) -> None:
    opened.select_project("beta")  # its README has no concepts

    assert "no concepts or key findings" in opened.views.study.card.toPlainText()
