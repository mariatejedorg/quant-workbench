"""Virtual environments: inspecting whether a project can run, and building or repairing them."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quant_workbench.application.jobs import JobExecutor, JobSpec
from quant_workbench.domain.ports import (
    InterpreterResolver,
    ProcessRunner,
    ProjectFiles,
)
from quant_workbench.domain.process import ProcessSpec
from quant_workbench.domain.project import Project
from quant_workbench.domain.requirements import (
    Requirement,
    missing_requirements,
    normalize_name,
    parse_requirements,
)
from quant_workbench.domain.runs import JobKind, LogStream, Run

_INSPECT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class EnvironmentStatus:
    """Whether a project's environment exists and satisfies its ``requirements.txt``."""

    interpreter: Path | None
    python_version: str | None
    installed: Mapping[str, str]  # normalised package name -> version
    requirements_found: bool
    missing: tuple[Requirement, ...]

    @property
    def exists(self) -> bool:
        return self.interpreter is not None

    @property
    def healthy(self) -> bool:
        return self.exists and not self.missing


class EnvironmentService:
    """Use case: inspect and set up the per-project virtual environments."""

    def __init__(
        self,
        *,
        executor: JobExecutor,
        runner: ProcessRunner,
        interpreters: InterpreterResolver,
        files: ProjectFiles,
        base_python: Path,
        default_timeout_seconds: int,
    ) -> None:
        self._executor = executor
        self._runner = runner
        self._interpreters = interpreters
        self._files = files
        self._base_python = base_python
        self._timeout = default_timeout_seconds

    async def inspect(self, project: Project) -> EnvironmentStatus:
        """Compare what the project's environment has installed with what it requires.

        This asks the project's *own* interpreter (``pip list --format=json``), so an
        environment whose installation was interrupted is detected as missing packages
        rather than assumed complete because the folder exists.
        """
        requirements_text = self._files.read_text(project, project.spec.requirements)
        required = parse_requirements(requirements_text) if requirements_text is not None else ()

        interpreter = self._interpreters.find(project)
        if interpreter is None:
            return EnvironmentStatus(None, None, {}, requirements_text is not None, required)

        version = await self._capture(
            interpreter, project, "-c", "import sys; print(sys.version.split()[0])"
        )
        listing = await self._capture(interpreter, project, "-m", "pip", "list", "--format=json")
        installed = self._parse_pip_list(listing)
        return EnvironmentStatus(
            interpreter=interpreter,
            python_version=version.strip() or None,
            installed=installed,
            requirements_found=requirements_text is not None,
            missing=missing_requirements(required, installed),
        )

    async def setup(self, project: Project) -> Run:
        """Create the virtual environment if absent, then install the requirements.

        Recorded and streamed like any other job. If the environment already exists this
        only (re)runs the installation, which is exactly the repair for an interrupted
        ``pip install``.
        """
        steps: list[ProcessSpec] = []
        interpreter = self._interpreters.find(project)
        if interpreter is None:
            steps.append(
                ProcessSpec(
                    (str(self._base_python), "-m", "venv", str(project.venv_dir)), project.root
                )
            )
            interpreter = project.venv_python
        if self._files.read_text(project, project.spec.requirements) is not None:
            steps.append(
                ProcessSpec(
                    (
                        str(interpreter),
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "-r",
                        str(project.requirements_path),
                    ),
                    project.root,
                )
            )
        return await self._executor.execute(
            JobSpec(
                project=project,
                kind=JobKind.ENV_SETUP,
                steps=tuple(steps),
                timeout_seconds=self._timeout,
                banner=f"Setting up the environment of {project.title}",
            )
        )

    async def _capture(self, interpreter: Path, project: Project, *args: str) -> str:
        lines: list[str] = []

        def collect(stream: LogStream, text: str) -> None:
            if stream is LogStream.STDOUT:
                lines.append(text)

        await self._runner.run(
            ProcessSpec((str(interpreter), *args), project.root),
            on_line=collect,
            timeout_seconds=_INSPECT_TIMEOUT_SECONDS,
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_pip_list(text: str) -> dict[str, str]:
        try:
            entries = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return {normalize_name(entry["name"]): entry["version"] for entry in entries}
