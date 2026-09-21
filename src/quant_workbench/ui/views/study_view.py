"""Study tab: flash cards from the project's README, scheduled with spaced repetition."""

from __future__ import annotations

from html import escape

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from quant_workbench.application.study import StudyItem
from quant_workbench.domain.study import Grade
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView

#: The usual again / hard / good / easy buttons and the SM-2 grade each one records.
_GRADES = (("Again", Grade.WRONG), ("Hard", Grade.HARD), ("Good", Grade.GOOD), ("Easy", Grade.EASY))


class StudyView(ProjectView):
    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self._queue: list[StudyItem] = []
        self._revealed = False
        self.progress = QLabel()
        self.card = QTextBrowser()
        self.reveal_button = QPushButton("Show answer")
        self.reveal_button.clicked.connect(self.reveal)
        self.grade_buttons: dict[Grade, QPushButton] = {}
        row = QHBoxLayout()
        row.addWidget(self.reveal_button)
        for label, grade in _GRADES:
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, g=grade: self.grade(g))
            self.grade_buttons[grade] = button
            row.addWidget(button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.progress)
        layout.addWidget(self.card, 1)
        layout.addLayout(row)
        self.refresh()

    # -------------------------------------------------------------------- session
    def refresh(self) -> None:
        """Rebuild today's queue for the selected project."""
        project = self._project
        self._queue = self._controller.container.study.queue([project]) if project else []
        self._revealed = False
        self._show()

    def current(self) -> StudyItem | None:
        return self._queue[0] if self._queue else None

    def reveal(self) -> None:
        self._revealed = True
        self._show()

    def grade(self, grade: Grade) -> None:
        """Record the answer to the current card and move to the next one."""
        item = self.current()
        if item is None or not self._revealed:
            return
        self._controller.container.study.answer(item, grade)
        self._queue.pop(0)
        self._revealed = False
        self._show()

    def _show(self) -> None:
        t = self._tokens
        item = self.current()
        if self._project is None:
            self.progress.setText("")
            self.card.setHtml("<p>Select a project to study its README.</p>")
        elif item is None:
            stats = self._controller.container.study.summary([self._project])[self._project.slug]
            self.progress.setText(f"{stats.total} card(s), {stats.learned} learned")
            self.card.setHtml(
                f"<p style='color:{t.success}'><b>Nothing due today.</b></p>"
                if stats.total
                else "<p>This README has no concepts or key findings to study yet.</p>"
            )
        else:
            self.progress.setText(f"{len(self._queue)} card(s) left today - {item.card.kind}")
            body = f"<h3>{escape(item.card.prompt)}</h3>"
            if self._revealed:
                body += f"<hr><p>{escape(item.card.answer)}</p>"
            else:
                body += f"<p style='color:{t.ink_muted}'>Answer it out loud first, then reveal.</p>"
            self.card.setHtml(body)
        studying = item is not None
        self.reveal_button.setEnabled(studying and not self._revealed)
        for button in self.grade_buttons.values():
            button.setEnabled(studying and self._revealed)
