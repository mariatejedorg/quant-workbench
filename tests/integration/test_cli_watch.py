"""`qw watch`: the command around the watch service (the service itself has its own tests)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_workbench.application.watch import WatchedRun, WatchService
from quant_workbench.cli.main import app
from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.runs import JobKind, Run, RunStatus
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

runner = CliRunner()


def qw(workspace: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["--home", str(workspace / "home"), *args, "-w", str(workspace)])
    return result.exit_code, result.output


def test_a_project_without_source_folders_is_an_expected_failure(accented_root: Path) -> None:
    write_project(accented_root, "alpha", manifest=manifest_toml("alpha"), src_main=False)
    shutil.rmtree(accented_root / "alpha" / "src")

    code, output = qw(accented_root, "watch", "alpha")

    assert code == 2
    assert "no src/ or config folder" in output


def test_it_reports_each_run_and_stops_quietly_on_ctrl_c(
    accented_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(accented_root, "alpha", manifest=manifest_toml("alpha"))

    async def fake_watch(self, project, *, options, initial, on_run):  # type: ignore[no-untyped-def]
        run = Run(
            id=RunId("0000000000001-aaaaaaaa"),
            project=Slug("alpha"),
            kind=JobKind.RUN,
            status=RunStatus.SUCCEEDED,
            queued_at=datetime.now(UTC),
        )
        on_run(WatchedRun(1, run, ()))
        on_run(WatchedRun(2, run, ("src/main.py",)))
        raise KeyboardInterrupt

    monkeypatch.setattr(WatchService, "watch", fake_watch)

    code, output = qw(accented_root, "watch", "alpha", "-q")

    assert code == 0, output
    assert "run #1" in output
    assert "first run" in output
    assert "run #2" in output
    assert "changed: src/main.py" in output
    assert "Stopped" in output
