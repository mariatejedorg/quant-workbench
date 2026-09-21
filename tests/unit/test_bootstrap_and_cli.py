from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from quant_workbench import __version__
from quant_workbench.application.settings import Settings
from quant_workbench.bootstrap import build_container
from quant_workbench.cli.main import app
from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.paths import ensure_app_dirs
from tests.conftest import FakeClock

runner = CliRunner()


def test_container_wires_injected_dependencies(app_paths: AppPaths, clock: FakeClock) -> None:
    settings = Settings(max_concurrency=2)

    container = build_container(paths=app_paths, settings=settings, clock=clock)

    assert container.settings.max_concurrency == 2
    assert container.clock is clock
    assert container.paths == app_paths


def test_container_reads_settings_from_the_config_dir(app_paths: AppPaths) -> None:
    app_paths.settings_file.write_text("max_concurrency = 4\n", encoding="utf-8")

    assert build_container(paths=app_paths).settings.max_concurrency == 4


def test_app_paths_under_a_root_are_hermetic(accented_root: Path) -> None:
    paths = AppPaths.under(accented_root)
    ensure_app_dirs(paths)

    assert paths.database_file.parent.is_dir()
    assert all(
        accented_root in p.parents for p in (paths.config_dir, paths.data_dir, paths.log_dir)
    )


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_info_lists_the_resolved_environment(accented_root: Path) -> None:
    result = runner.invoke(app, ["--home", str(accented_root), "info"])

    assert result.exit_code == 0
    assert "mariatg.invers@gmail.com" in result.output
    assert "workspace" in result.output


def test_cli_without_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "info" in result.output
