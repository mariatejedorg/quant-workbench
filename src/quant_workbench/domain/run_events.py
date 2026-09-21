"""Events describing the life of a run and of a batch of runs."""

from __future__ import annotations

from dataclasses import dataclass

from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.process import ResourceSample
from quant_workbench.domain.runs import LogLine, Run


@dataclass(frozen=True, slots=True)
class RunQueued(DomainEvent):
    run: Run


@dataclass(frozen=True, slots=True)
class RunStarted(DomainEvent):
    run: Run


@dataclass(frozen=True, slots=True)
class RunOutput(DomainEvent):
    run_id: RunId
    project: Slug
    line: LogLine


@dataclass(frozen=True, slots=True)
class RunResourcesSampled(DomainEvent):
    run_id: RunId
    project: Slug
    sample: ResourceSample


@dataclass(frozen=True, slots=True)
class RunFinished(DomainEvent):
    """Published once per attempt when it reaches a terminal state."""

    run: Run


@dataclass(frozen=True, slots=True)
class BatchProgress(DomainEvent):
    """How far a batch (``run-all``) has got: ``done`` of ``total`` runs are finished."""

    done: int
    total: int
    current_layer: int
    layers: int
