from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.application.config import ConfigService
from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.domain.literals import ValueKind
from quant_workbench.domain.project import ConfigTarget
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor
from tests.fakes import MemoryFiles, make_project

CONFIG = 'TICKER = "AAPL"\r\nWINDOW = 252  # sessions\r\nRATE = 0.045\r\n'
DATA = 'TICKERS = {"A": "a"}\r\nPERIOD = "3y"\r\nOTHER = 1\r\n'


def service_with(files: dict[str, str]) -> tuple[ConfigService, MemoryFiles]:
    memory = MemoryFiles(files)
    return ConfigService(files=memory, editor=LibCstConfigEditor()), memory


def test_constants_are_collected_from_every_declared_file(tmp_path: Path) -> None:
    service, _ = service_with({"config/a.py": CONFIG, "src/data.py": DATA})
    alpha = make_project(
        tmp_path,
        "alpha",
        config_targets=[
            ConfigTarget("config/a.py"),
            ConfigTarget("src/data.py", symbols=("TICKERS", "PERIOD")),
            ConfigTarget("config/missing.py"),  # a declared file that does not exist: skipped
        ],
    )

    found = service.constants(alpha)

    assert [c.qualified_name for c in found] == [
        "config/a.py:TICKER",
        "config/a.py:WINDOW",
        "config/a.py:RATE",
        "src/data.py:TICKERS",
        "src/data.py:PERIOD",  # OTHER is not a declared symbol
    ]


def test_planning_produces_a_diff_and_touches_nothing(tmp_path: Path) -> None:
    service, files = service_with({"config/a.py": CONFIG})
    alpha = make_project(tmp_path, "alpha", config_files=["config/a.py"])

    plan = service.plan(alpha, {"WINDOW": 126})

    assert not plan.is_empty
    assert files.writes == []
    assert files.files["config/a.py"] == CONFIG
    assert "--- a/config/a.py" in plan.diff
    assert "+++ b/config/a.py" in plan.diff
    assert "-WINDOW = 252  # sessions" in plan.diff
    assert "+WINDOW = 126  # sessions" in plan.diff


def test_a_plan_that_changes_nothing_is_empty(tmp_path: Path) -> None:
    service, _ = service_with({"config/a.py": CONFIG})
    alpha = make_project(tmp_path, "alpha", config_files=["config/a.py"])

    plan = service.plan(alpha, {"WINDOW": 252})

    assert plan.is_empty
    assert plan.diff == ""


def test_applying_writes_the_new_text_byte_for_byte(tmp_path: Path) -> None:
    service, files = service_with({"config/a.py": CONFIG})
    alpha = make_project(tmp_path, "alpha", config_files=["config/a.py"])

    result = service.set(alpha, {"WINDOW": 126, "RATE": 0.05})

    assert files.files["config/a.py"] == CONFIG.replace("252", "126").replace("0.045", "0.05")
    assert result.files == ("config/a.py",)
    assert result.backups == (Path("backups/config/a.py"),)


def test_changes_to_several_files_are_planned_together(tmp_path: Path) -> None:
    service, files = service_with({"config/a.py": CONFIG, "src/data.py": DATA})
    alpha = make_project(
        tmp_path,
        "alpha",
        config_files=["config/a.py"],
        config_targets=[ConfigTarget("src/data.py")],
    )

    plan = service.plan(alpha, {"WINDOW": 10, "PERIOD": "5y"})
    service.apply(alpha, plan)

    assert {change.file for change in plan.changes} == {"config/a.py", "src/data.py"}
    assert 'PERIOD = "5y"' in files.files["src/data.py"]


def test_a_file_edited_after_planning_is_never_overwritten(tmp_path: Path) -> None:
    service, files = service_with({"config/a.py": CONFIG})
    alpha = make_project(tmp_path, "alpha", config_files=["config/a.py"])
    plan = service.plan(alpha, {"WINDOW": 126})
    files.files["config/a.py"] = CONFIG + "EXTRA = 1\r\n"  # the user edited it meanwhile

    with pytest.raises(UnsafeEditError, match="changed on disk"):
        service.apply(alpha, plan)

    assert files.writes == []


def test_names_can_be_qualified_with_their_file(tmp_path: Path) -> None:
    service, _ = service_with({"a.py": "X = 1\n", "b.py": "X = 2\n"})
    alpha = make_project(tmp_path, "alpha", config_files=["a.py", "b.py"])

    with pytest.raises(UnsafeEditError, match="several files") as raised:
        service.plan(alpha, {"X": 5})
    assert raised.value.hint is not None
    assert "a.py:X" in raised.value.hint

    plan = service.plan(alpha, {"b.py:X": 5})
    assert [change.file for change in plan.changes] == ["b.py"]


def test_unknown_names_are_reported_with_suggestions(tmp_path: Path) -> None:
    service, _ = service_with({"config/a.py": CONFIG})
    alpha = make_project(tmp_path, "alpha", config_files=["config/a.py"])

    with pytest.raises(UnsafeEditError, match="No editable constant named WINDW") as raised:
        service.plan(alpha, {"WINDW": 1})

    assert raised.value.hint is not None
    assert "WINDOW" in raised.value.hint


def test_text_assignments_are_typed_by_the_constants_they_target(tmp_path: Path) -> None:
    service, _ = service_with({"config/a.py": CONFIG})
    alpha = make_project(tmp_path, "alpha", config_files=["config/a.py"])

    values = service.parse_assignments(alpha, {"WINDOW": "10", "RATE": "0.1", "TICKER": "MSFT"})

    assert values == {"WINDOW": 10, "RATE": 0.1, "TICKER": "MSFT"}
    assert service.constants(alpha)[0].constant.kind is ValueKind.STR
    with pytest.raises(UnsafeEditError, match="not a valid int"):
        service.parse_assignments(alpha, {"WINDOW": "ten"})


def test_files_that_are_not_valid_utf8_are_refused(tmp_path: Path) -> None:
    service, _ = service_with({"a.py": "X = 1  # caf�\n"})  # what errors="replace" yields
    alpha = make_project(tmp_path, "alpha", config_files=["a.py"])

    with pytest.raises(UnsafeEditError, match="not valid UTF-8"):
        service.plan(alpha, {"X": 2})
