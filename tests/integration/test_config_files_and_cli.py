"""Editing configuration on a real disk: atomic writes, backups, and the ``qw config`` commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_workbench.cli.main import app
from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.infrastructure.project_files import FileSystemProjectFiles
from tests.conftest import FakeClock
from tests.fakes import make_project
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

runner = CliRunner()

CONFIG = 'TICKER = "AAPL"  # ticker\r\nWINDOW = 252\r\nTICKERS = {\r\n    "A": "a",\r\n    "B": "b",\r\n}\r\n'


# --------------------------------------------------------------- the file adapter
def test_reading_keeps_windows_line_endings(accented_root: Path) -> None:
    project = make_project(accented_root, "alpha")
    target = project.root / "config" / "c.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(CONFIG.encode("utf-8"))

    assert FileSystemProjectFiles().read_text(project, "config/c.py") == CONFIG


def test_writing_is_byte_exact_and_keeps_a_backup(accented_root: Path) -> None:
    project = make_project(accented_root, "alpha")
    target = project.root / "config" / "c.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(CONFIG.encode("utf-8"))
    clock = FakeClock(datetime(2026, 9, 21, 12, 30, 5, tzinfo=UTC))
    files = FileSystemProjectFiles(backups=accented_root / "backups", clock=clock)

    backup = files.write_text(project, "config/c.py", CONFIG.replace("252", "126"))

    assert target.read_bytes() == CONFIG.replace("252", "126").encode("utf-8")
    assert backup is not None
    assert (
        backup == accented_root / "backups" / "alpha" / "20260921T123005000000" / "config" / "c.py"
    )
    assert backup.read_bytes() == CONFIG.encode("utf-8")
    assert list(target.parent.glob(".*qw-tmp")) == []  # no temporary file left behind


def test_writing_a_new_file_needs_no_backup(accented_root: Path) -> None:
    project = make_project(accented_root, "alpha")
    (project.root / "config").mkdir(parents=True)
    files = FileSystemProjectFiles(backups=accented_root / "backups", clock=FakeClock())

    assert files.write_text(project, "config/new.py", "X = 1\n") is None
    assert (project.root / "config" / "new.py").read_text(encoding="utf-8") == "X = 1\n"
    assert not (accented_root / "backups").exists()


def test_writing_outside_the_project_is_refused(accented_root: Path) -> None:
    project = make_project(accented_root, "alpha")
    project.root.mkdir(parents=True)

    with pytest.raises(UnsafeEditError, match="outside"):
        FileSystemProjectFiles().write_text(project, "../escape.py", "X = 1\n")

    assert not (accented_root / "escape.py").exists()


# ------------------------------------------------------------------------ the CLI
@pytest.fixture
def workspace(accented_root: Path) -> Path:
    for name in ("alpha", "beta"):
        write_project(
            accented_root,
            name,
            manifest=manifest_toml(name)
            + '\n[[config_targets]]\nfile = "config/settings.py"\nsymbols = []\n',
            extra_files={"config/settings.py": CONFIG},
        )
        # write_text would have translated the newlines on Windows: restore the CRLF originals
        (accented_root / name / "config" / "settings.py").write_bytes(CONFIG.encode("utf-8"))
    return accented_root


def qw(workspace: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["--home", str(workspace / "home"), *args, "-w", str(workspace)])
    return result.exit_code, result.output


def settings_file(workspace: Path) -> Path:
    return workspace / "alpha" / "config" / "settings.py"


def test_get_lists_constants_with_type_value_and_description(workspace: Path) -> None:
    code, output = qw(workspace, "config", "get", "alpha")

    assert code == 0, output
    for expected in ("TICKER", "WINDOW", "TICKERS", "int", "dict", "252", "ticker"):
        assert expected in output


def test_get_json_is_machine_readable(workspace: Path) -> None:
    code, output = qw(workspace, "config", "get", "alpha", "WINDOW", "--json")

    assert code == 0, output
    (entry,) = json.loads(output)
    assert entry["name"] == "WINDOW"
    assert entry["type"] == "int"
    assert entry["source"] == "252"


def test_diff_previews_without_writing(workspace: Path) -> None:
    before = settings_file(workspace).read_bytes()

    code, output = qw(workspace, "config", "diff", "alpha", "WINDOW=126")

    assert code == 0, output
    assert "-WINDOW = 252" in output
    assert "+WINDOW = 126" in output
    assert settings_file(workspace).read_bytes() == before


def test_set_edits_the_file_keeping_everything_else_and_backs_it_up(workspace: Path) -> None:
    code, output = qw(workspace, "config", "set", "alpha", "WINDOW=126", "TICKER=MSFT")

    assert code == 0, output
    expected = CONFIG.replace("252", "126").replace('"AAPL"', '"MSFT"')
    assert settings_file(workspace).read_bytes() == expected.encode("utf-8")
    assert "Updated" in output
    backups = list((workspace / "home" / "data" / "backups" / "alpha").rglob("settings.py"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == CONFIG.encode("utf-8")
    # the other project is untouched
    assert (workspace / "beta" / "config" / "settings.py").read_bytes() == CONFIG.encode("utf-8")


def test_set_understands_python_syntax_for_containers(workspace: Path) -> None:
    code, output = qw(workspace, "config", "set", "alpha", "TICKERS={'A': 'a', 'C': 'c'}")

    assert code == 0, output
    text = settings_file(workspace).read_bytes().decode("utf-8")
    assert text == CONFIG.replace('    "B": "b",\r\n', '    "C": "c",\r\n')


def test_dry_run_and_no_op_write_nothing(workspace: Path) -> None:
    before = settings_file(workspace).read_bytes()

    code, output = qw(workspace, "config", "set", "alpha", "WINDOW=126", "--dry-run")
    assert code == 0, output
    assert "+WINDOW = 126" in output
    assert settings_file(workspace).read_bytes() == before

    code, output = qw(workspace, "config", "set", "alpha", "WINDOW=252")
    assert code == 0, output
    assert "No changes" in output
    assert not (workspace / "home" / "data" / "backups").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("WINDOW=abc",), "not a valid int"),
        (("WINDW=1",), "No editable constant named WINDW"),
        (("WINDOW",), "is not an assignment"),
    ],
)
def test_bad_assignments_are_expected_failures_that_write_nothing(
    workspace: Path, args: tuple[str, ...], message: str
) -> None:
    before = settings_file(workspace).read_bytes()

    code, output = qw(workspace, "config", "set", "alpha", *args)

    assert code == 2
    assert message in output
    assert settings_file(workspace).read_bytes() == before
