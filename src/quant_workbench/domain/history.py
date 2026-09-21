"""Comparing runs and following a metric over time (pure functions over stored runs)."""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from quant_workbench.domain.runs import MetricValue, Run, RunStatus


@dataclass(frozen=True, slots=True)
class MetricChange:
    """One metric in two runs. ``delta`` is set when both values are numbers."""

    name: str
    before: float | int | str | None
    after: float | int | str | None
    unit: str = ""

    @property
    def delta(self) -> float | None:
        if isinstance(self.before, int | float) and isinstance(self.after, int | float):
            return float(self.after) - float(self.before)
        return None

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class OutputChange:
    """What happened to one declared output file between two runs."""

    path: str
    kind: str  # "added", "removed", "changed" or "same"


@dataclass(frozen=True, slots=True)
class RunComparison:
    """Everything that differs between an older run and a newer one."""

    before: Run
    after: Run
    #: ``(file, unified diff)`` for each configuration file whose text differs.
    config_diffs: tuple[tuple[str, str], ...]
    metrics: tuple[MetricChange, ...]
    outputs: tuple[OutputChange, ...]

    @property
    def status_changed(self) -> bool:
        return self.before.status is not self.after.status

    @property
    def duration_delta(self) -> timedelta | None:
        if self.before.duration is None or self.after.duration is None:
            return None
        return self.after.duration - self.before.duration

    @property
    def config_changed(self) -> bool:
        return bool(self.config_diffs)

    @property
    def changed_metrics(self) -> tuple[MetricChange, ...]:
        return tuple(m for m in self.metrics if m.changed)

    @property
    def changed_outputs(self) -> tuple[OutputChange, ...]:
        return tuple(o for o in self.outputs if o.kind != "same")


def compare_runs(before: Run, after: Run) -> RunComparison:
    """Diff the configuration, metrics and outputs of two runs of the same project."""
    return RunComparison(
        before=before,
        after=after,
        config_diffs=_config_diffs(before, after),
        metrics=_metric_changes(before.metrics, after.metrics),
        outputs=_output_changes(before, after),
    )


def _config_diffs(before: Run, after: Run) -> tuple[tuple[str, str], ...]:
    old, new = dict(before.config_snapshot), dict(after.config_snapshot)
    diffs: list[tuple[str, str]] = []
    for file in sorted(old.keys() | new.keys()):
        left, right = old.get(file), new.get(file)
        if left == right:
            continue
        text = "".join(
            difflib.unified_diff(
                (left or "").splitlines(keepends=True),
                (right or "").splitlines(keepends=True),
                fromfile=f"a/{file}" if left is not None else "/dev/null",
                tofile=f"b/{file}" if right is not None else "/dev/null",
            )
        )
        diffs.append((file, text))
    return tuple(diffs)


def _metric_changes(
    before: Sequence[MetricValue], after: Sequence[MetricValue]
) -> tuple[MetricChange, ...]:
    old = {m.name: m for m in before}
    new = {m.name: m for m in after}
    names = list(dict.fromkeys([*old, *new]))
    return tuple(
        MetricChange(
            name,
            old[name].value if name in old else None,
            new[name].value if name in new else None,
            (new.get(name) or old[name]).unit,
        )
        for name in names
    )


def _output_changes(before: Run, after: Run) -> tuple[OutputChange, ...]:
    old = {o.path: o.sha256 for o in before.outputs}
    new = {o.path: o.sha256 for o in after.outputs}
    changes: list[OutputChange] = []
    for path in sorted(old.keys() | new.keys()):
        if path not in old:
            kind = "added"
        elif path not in new:
            kind = "removed"
        else:
            kind = "same" if old[path] == new[path] else "changed"
        changes.append(OutputChange(path, kind))
    return tuple(changes)


def metric_names(runs: Sequence[Run]) -> list[str]:
    """Names of every numeric metric that appears in any run, in first-seen order."""
    seen: dict[str, None] = {}
    for run in runs:
        for metric in run.metrics:
            if isinstance(metric.value, int | float):
                seen.setdefault(metric.name)
    return list(seen)


def metric_series(runs: Sequence[Run], name: str) -> list[tuple[datetime, float]]:
    """``(time, value)`` of a numeric metric over the *successful* runs, oldest first.

    Failed and cancelled runs are left out on purpose: their numbers (if any) are partial and
    would put false jumps in a trend.
    """
    points = [
        (run.finished_at or run.queued_at, float(metric.value))
        for run in runs
        if run.status is RunStatus.SUCCEEDED
        and (metric := run.metric(name)) is not None
        and isinstance(metric.value, int | float)
    ]
    return sorted(points, key=lambda point: point[0])
