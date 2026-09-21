"""Git panel: branch, uncommitted files with their diff, recent commits, guarded commit.

There is intentionally no Push button. The panel shows the command instead: publishing
commits changes a remote, and that decision stays with the user.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.application.git_service import GitOverview
from quant_workbench.domain.project import Project
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.diffhtml import diff_html
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView

_PATH_ROLE = Qt.ItemDataRole.UserRole + 1


class GitView(ProjectView):
    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self._overview: GitOverview | None = None
        self.header = QLabel("Select a project.")
        self.header.setWordWrap(True)
        self.header.setTextFormat(Qt.TextFormat.RichText)

        self.changes = QListWidget()
        self.changes.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.changes.currentItemChanged.connect(self._show_diff)
        self.diff = QTextBrowser()
        self.commits = QListWidget()
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText("Commit message…")
        self.message.setFixedHeight(70)
        self.message.textChanged.connect(self._update_buttons)
        self.commit_button = QPushButton("Commit")
        self.commit_button.clicked.connect(self.commit)
        self.push_button = QPushButton("Copy push command")
        self.push_button.clicked.connect(self.copy_push_command)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.notice = QLabel()
        self.notice.setWordWrap(True)

        top = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Uncommitted changes (select some to commit only those)"))
        left_layout.addWidget(self.changes)
        left_layout.addWidget(QLabel("Recent commits"))
        left_layout.addWidget(self.commits)
        top.addWidget(left)
        top.addWidget(self.diff)
        top.setStretchFactor(1, 2)

        buttons = QHBoxLayout()
        buttons.addWidget(self.commit_button)
        buttons.addWidget(self.push_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(top, 1)
        layout.addWidget(self.message)
        layout.addLayout(buttons)
        layout.addWidget(self.notice)
        self._token = 0  # answers to an older selection are ignored
        self.refresh()

    # ------------------------------------------------------------------ loading
    def refresh(self) -> None:
        self._token += 1
        project = self._project
        self._overview = None
        self.changes.clear()
        self.commits.clear()
        self.diff.setHtml("")
        if project is None:
            self.header.setText("Select a project.")
            self._update_buttons()
            return
        self.header.setText("Reading the repository…")
        token = self._token
        service = self._controller.container.git_service
        self._controller.run_blocking(
            lambda: service.overview(project), lambda o: self._show_overview(token, project, o)
        )
        self._update_buttons()

    def _show_overview(self, token: int, project: Project, overview: GitOverview | None) -> None:
        if token != self._token:
            return
        self._overview = overview
        if overview is None:
            self.header.setText(f"{project.title} is not a git repository of its own.")
            self._update_buttons()
            return
        state, t = overview.state, self._tokens
        identity = (
            f"<span style='color:{t.success}'>identity ok ({overview.email})</span>"
            if overview.identity_ok
            else f"<span style='color:{t.error}'>wrong identity: {overview.email or 'none'}</span>"
        )
        sync = (
            f"ahead {state.ahead}, behind {state.behind}" if state.has_upstream else "no upstream"
        )
        self.header.setText(
            f"<b>{state.branch or '(detached HEAD)'}</b> - {sync} - "
            f"{'clean' if state.is_clean else f'{len(state.dirty)} changed'} - {identity}"
        )
        for path in state.dirty:
            item = QListWidgetItem(path)
            item.setData(_PATH_ROLE, path)
            self.changes.addItem(item)
        for commit in overview.commits:
            self.commits.addItem(f"{commit.short}  {commit.subject}  ({commit.author})")
        self._update_buttons()

    def _show_diff(self, item: QListWidgetItem | None) -> None:
        if item is None or self._project is None:
            return
        token, project = self._token, self._project
        service = self._controller.container.git_service
        path = str(item.data(_PATH_ROLE))

        def show(text: str) -> None:
            if token == self._token:
                self.diff.setHtml(diff_html(text, self._tokens))

        self._controller.run_blocking(lambda: service.diff(project, path), show)

    # ------------------------------------------------------------------- actions
    def _update_buttons(self) -> None:
        overview = self._overview
        ready = (
            overview is not None
            and overview.identity_ok
            and bool(overview.state.dirty)
            and bool(self.message.toPlainText().strip())
        )
        self.commit_button.setEnabled(ready)
        self.push_button.setEnabled(overview is not None)
        if overview is not None and not overview.identity_ok:
            self.notice.setText(
                "Commits are blocked: this repository would author them with the wrong identity. "
                "Fix it from the Problems panel (or `qw doctor --fix`)."
            )
        elif self.notice.text().startswith("Commits are blocked"):
            self.notice.setText("")  # the identity was fixed; other notices (e.g. "Committed") stay

    def selected_paths(self) -> list[str]:
        return [str(i.data(_PATH_ROLE)) for i in self.changes.selectedItems()]

    def commit(self) -> None:
        """Commit the selected files (all changes when none is selected)."""
        project, overview = self._project, self._overview
        if project is None or overview is None:
            return
        text, paths = self.message.toPlainText(), self.selected_paths()
        service = self._controller.container.git_service

        def done(short: str) -> None:
            self.message.clear()
            self.notice.setText(f"Committed {short}. Nothing was pushed.")
            self.refresh()

        self._controller.run_blocking(lambda: service.commit(project, text, paths), done)

    def push_command(self) -> str:
        return self._overview.push_command if self._overview else ""

    def copy_push_command(self) -> None:
        QApplication.clipboard().setText(self.push_command())
        self.notice.setText("Command copied. Run it yourself when you want to publish.")

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
