from __future__ import annotations

from quant_workbench.domain.errors import DependencyCycleError, WorkbenchError


def test_str_includes_the_hint_when_present() -> None:
    assert str(WorkbenchError("boom")) == "boom"
    assert str(WorkbenchError("boom", hint="try again")) == "boom (hint: try again)"


def test_cycle_error_names_the_full_loop() -> None:
    error = DependencyCycleError(("a", "b", "c"))
    assert "a -> b -> c -> a" in str(error)
    assert error.cycle == ("a", "b", "c")
