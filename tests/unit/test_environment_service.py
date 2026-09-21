from __future__ import annotations

import json
from pathlib import Path

from quant_workbench.application.environments import EnvironmentService
from quant_workbench.application.jobs import JobExecutor
from quant_workbench.domain.process import ProcessSpec
from quant_workbench.domain.runs import JobKind, LogStream, RunStatus
from quant_workbench.infrastructure.event_bus import InProcessEventBus
from tests.conftest import FakeClock
from tests.fakes import (
    FixedInterpreter,
    MemoryFiles,
    MemoryRepository,
    Script,
    ScriptedRunner,
    make_project,
)

BASE_PYTHON = Path("base-python")
VENV_PYTHON = Path("venv-python")


def pip_list(**packages: str) -> str:
    return json.dumps([{"name": n, "version": v} for n, v in packages.items()])


def build(
    tmp: Path,
    clock: FakeClock,
    runner: ScriptedRunner,
    files: MemoryFiles,
    interpreter: Path | None,
) -> tuple[EnvironmentService, MemoryRepository]:
    repo = MemoryRepository()
    executor = JobExecutor(
        runner=runner,
        repository=repo,
        files=files,
        events=InProcessEventBus(),
        clock=clock,
        max_concurrency=2,
    )
    service = EnvironmentService(
        executor=executor,
        runner=runner,
        interpreters=FixedInterpreter(interpreter),
        files=files,
        base_python=BASE_PYTHON,
        default_timeout_seconds=600,
    )
    return service, repo


async def test_inspect_reports_missing_packages(tmp_path: Path, clock: FakeClock) -> None:
    def script_for(spec: ProcessSpec) -> Script:
        if "pip" in spec.args:
            return Script(lines=[(LogStream.STDOUT, pip_list(numpy="2.0", Pandas="2.2"))])
        return Script(lines=[(LogStream.STDOUT, "3.13.1")])

    files = MemoryFiles({"requirements.txt": "numpy\npandas\nyfinance\nplotly>=5\n"})
    service, _ = build(tmp_path, clock, ScriptedRunner(script_for), files, VENV_PYTHON)

    status = await service.inspect(make_project(tmp_path, "alpha"))

    assert status.exists
    assert status.python_version == "3.13.1"
    assert [r.name for r in status.missing] == ["yfinance", "plotly"]
    assert not status.healthy
    assert set(status.installed) == {"numpy", "pandas"}


async def test_a_complete_environment_is_healthy(tmp_path: Path, clock: FakeClock) -> None:
    def script_for(spec: ProcessSpec) -> Script:
        text = pip_list(numpy="2.0") if "pip" in spec.args else "3.12.4"
        return Script(lines=[(LogStream.STDOUT, text)])

    service, _ = build(
        tmp_path,
        clock,
        ScriptedRunner(script_for),
        MemoryFiles({"requirements.txt": "numpy\n"}),
        VENV_PYTHON,
    )

    assert (await service.inspect(make_project(tmp_path, "alpha"))).healthy


async def test_inspect_without_an_environment_does_not_start_any_process(
    tmp_path: Path, clock: FakeClock
) -> None:
    runner = ScriptedRunner()
    service, _ = build(tmp_path, clock, runner, MemoryFiles({"requirements.txt": "numpy\n"}), None)

    status = await service.inspect(make_project(tmp_path, "alpha"))

    assert not status.exists
    assert not status.healthy
    assert [r.name for r in status.missing] == ["numpy"]
    assert runner.calls == []


async def test_garbage_from_pip_is_treated_as_nothing_installed(
    tmp_path: Path, clock: FakeClock
) -> None:
    runner = ScriptedRunner(Script(lines=[(LogStream.STDOUT, "not json at all")]))
    service, _ = build(
        tmp_path, clock, runner, MemoryFiles({"requirements.txt": "numpy\n"}), VENV_PYTHON
    )

    status = await service.inspect(make_project(tmp_path, "alpha"))

    assert status.installed == {}
    assert [r.name for r in status.missing] == ["numpy"]


async def test_setup_creates_the_venv_then_installs(tmp_path: Path, clock: FakeClock) -> None:
    runner = ScriptedRunner()
    service, repo = build(
        tmp_path, clock, runner, MemoryFiles({"requirements.txt": "numpy\n"}), interpreter=None
    )
    project = make_project(tmp_path, "alpha")

    run = await service.setup(project)

    assert run.status is RunStatus.SUCCEEDED
    assert run.kind is JobKind.ENV_SETUP
    create, install = (spec.args for spec in runner.calls)
    assert create == (str(BASE_PYTHON), "-m", "venv", str(project.venv_dir))
    assert install[:4] == (str(project.venv_python), "-m", "pip", "install")
    assert install[-2:] == ("-r", str(project.requirements_path))
    assert repo.get(run.id) is not None


async def test_setup_of_an_existing_environment_only_repairs_the_installation(
    tmp_path: Path, clock: FakeClock
) -> None:
    runner = ScriptedRunner()
    service, _ = build(
        tmp_path, clock, runner, MemoryFiles({"requirements.txt": "numpy\n"}), VENV_PYTHON
    )

    await service.setup(make_project(tmp_path, "alpha"))

    (only,) = runner.calls
    assert only.args[0] == str(VENV_PYTHON)
    assert "pip" in only.args


async def test_setup_stops_at_the_first_failing_step(tmp_path: Path, clock: FakeClock) -> None:
    runner = ScriptedRunner(Script(exit_code=1, lines=[(LogStream.STDERR, "venv failed")]))
    service, _ = build(
        tmp_path, clock, runner, MemoryFiles({"requirements.txt": "numpy\n"}), interpreter=None
    )

    run = await service.setup(make_project(tmp_path, "alpha"))

    assert run.status is RunStatus.FAILED
    assert len(runner.calls) == 1  # pip was never attempted


async def test_setup_without_a_requirements_file_only_creates_the_venv(
    tmp_path: Path, clock: FakeClock
) -> None:
    runner = ScriptedRunner()
    service, _ = build(tmp_path, clock, runner, MemoryFiles(), interpreter=None)

    await service.setup(make_project(tmp_path, "alpha"))

    assert len(runner.calls) == 1
    assert "venv" in runner.calls[0].args
