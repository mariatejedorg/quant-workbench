"""The whole engine with real processes and a real database (no fakes)."""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from quant_workbench.application.jobs import JobExecutor
from quant_workbench.application.runs import RunOptions, RunService
from quant_workbench.domain.project import MetricExtractorSpec, MetricKind, Project
from quant_workbench.domain.runs import LogLevel, RunStatus
from quant_workbench.infrastructure.event_bus import InProcessEventBus
from quant_workbench.infrastructure.process import AsyncSubprocessRunner
from quant_workbench.infrastructure.project_files import FileSystemProjectFiles
from quant_workbench.infrastructure.resources import data_path
from quant_workbench.infrastructure.signatures import load_signatures
from quant_workbench.infrastructure.sqlite_runs import SqliteRunRepository, create_sqlite_engine
from tests.conftest import FakeClock
from tests.fakes import FixedInterpreter, make_project

pytestmark = pytest.mark.integration

PROJECT_MAIN = textwrap.dedent(
    """
    import sys
    print("=== Datos descargados ===")
    print("Distance to Default: 3.62")
    print("Probabilidad de impago real-world: 0.02%")
    print("Capitalización: 54,330,990,963")
    print("aviso: usando caché", file=sys.stderr)
    """
)
CONFIG = "TICKER = 'F'  # the company\nHORIZON = 1.0\n"


@pytest.fixture
def stack(accented_root: Path):  # type: ignore[no-untyped-def]
    """A RunService on real adapters, plus a project on disk with an accented path."""
    clock = FakeClock()
    repo = SqliteRunRepository(create_sqlite_engine(accented_root / "db.sqlite3"))
    events = InProcessEventBus()
    executor = JobExecutor(
        runner=AsyncSubprocessRunner(clock, sample_interval=0.1),
        repository=repo,
        files=FileSystemProjectFiles(),
        events=events,
        clock=clock,
        max_concurrency=2,
        signatures=load_signatures(data_path("failure_signatures")),
    )
    service = RunService(
        executor=executor,
        interpreters=FixedInterpreter(Path(sys.executable)),
        repository=repo,
        events=events,
        clock=clock,
        default_timeout_seconds=60,
        default_max_retries=0,
    )

    def project(main: str = PROJECT_MAIN, **kwargs: object) -> Project:
        made = make_project(accented_root, "credit-model", **kwargs)  # type: ignore[arg-type]
        (made.root / "src").mkdir(parents=True, exist_ok=True)
        (made.root / "config").mkdir(exist_ok=True)
        (made.root / "outputs").mkdir(exist_ok=True)
        (made.root / "src" / "main.py").write_text(main, encoding="utf-8")
        (made.root / "config" / "credit.py").write_bytes(CONFIG.encode("utf-8"))  # exact: no CRLF
        (made.root / "outputs" / "dashboard.html").write_text("<html>dash</html>", encoding="utf-8")
        return made

    yield service, repo, project
    repo.close()


async def test_a_real_run_extracts_metrics_snapshots_config_and_outputs(stack) -> None:  # type: ignore[no-untyped-def]
    service, repo, project = stack
    extractors = [
        MetricExtractorSpec("dd", r"Distance to Default:\s*([\d.]+)", MetricKind.FLOAT),
        MetricExtractorSpec("pd", r"real-world:\s*([\d.]+%)", MetricKind.PERCENT),
        MetricExtractorSpec("cap", r"Capitalización:\s*([\d,]+)", MetricKind.INT),
    ]

    run = await service.run(project(extractors=extractors, config_files=["config/credit.py"]))

    assert run.status is RunStatus.SUCCEEDED, run.failure
    assert {m.name: m.value for m in run.metrics} == {"dd": 3.62, "pd": 0.02, "cap": 54_330_990_963}
    assert run.config_snapshot == (("config/credit.py", CONFIG),)
    (output,) = run.outputs
    assert output.path == "outputs/dashboard.html"
    assert output.size == len("<html>dash</html>")
    assert len(output.sha256) == 64
    assert run.peak_rss_bytes > 0

    stored = repo.get(run.id)
    assert stored == run
    logs = [line.text for line in repo.logs(run.id)]
    assert "Capitalización: 54,330,990,963" in logs  # accents intact through pipe + database
    assert any(line.startswith("$ ") for line in logs)


async def test_a_real_failure_is_diagnosed_from_its_traceback(stack) -> None:  # type: ignore[no-untyped-def]
    service, repo, project = stack
    broken = "import scipy_that_does_not_exist\n"

    run = await service.run(project(broken))

    assert run.status is RunStatus.FAILED
    assert run.exit_code == 1
    assert run.signatures == ("module-not-found",)
    assert "not installed" in (run.failure or "")
    assert not run.transient
    assert LogLevel.ERROR in {line.level for line in repo.logs(run.id)}


async def test_a_real_timeout_kills_a_runaway_project(stack) -> None:  # type: ignore[no-untyped-def]
    service, _, project = stack
    started = time.monotonic()

    run = await service.run(
        project("import time; time.sleep(120)\n"), RunOptions(timeout_seconds=1)
    )

    assert run.status is RunStatus.TIMED_OUT
    assert time.monotonic() - started < 10


async def test_two_runs_of_the_same_project_are_kept_as_history(stack) -> None:  # type: ignore[no-untyped-def]
    service, repo, project = stack
    made = project()

    first = await service.run(made)
    second = await service.run(made)

    assert first.id != second.id
    runs = repo.list_runs(made.slug)
    assert {r.id for r in runs} == {first.id, second.id}
