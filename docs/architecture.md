# Architecture

Quant Workbench is a hexagonal application (see [ADR 0001](adr/0001-hexagonal-architecture.md)).
This page explains how the pieces fit together; the *why* behind each choice lives in the ADRs.

## Layers

```mermaid
flowchart TB
    subgraph delivery["Delivery"]
        cli["cli · Typer commands"]
        ui["ui · PySide6 desktop app"]
    end
    boot["bootstrap · composition root"]
    subgraph core["Core"]
        app["application · use cases"]
        dom["domain · entities, events, ports"]
    end
    infra["infrastructure · adapters"]

    cli --> app
    ui --> app
    app --> dom
    infra -->|implements ports| dom
    infra --> app
    boot -.wires.-> infra
    boot -.wires.-> app
    cli --> boot
    ui --> boot
```

The rule "dependencies point inwards" is enforced by `tests/unit/test_layering.py`, which
parses every module's imports. It also asserts that `domain` uses only the standard library
and that Qt is imported only under `ui`.

## Running a project

`RunService.run` and `EnvironmentService.setup` are thin callers of one class, the
`JobExecutor`, which owns the life cycle of every attempt:

```mermaid
sequenceDiagram
    participant C as Caller (CLI / GUI)
    participant R as RunService
    participant X as JobExecutor
    participant P as ProcessRunner
    participant DB as RunRepository
    participant B as EventBus

    C->>R: run(project)
    R->>X: execute(JobSpec)
    X->>DB: save(run: QUEUED)
    X->>B: RunQueued
    X->>X: wait for a concurrency slot
    X->>DB: save(run: RUNNING, config snapshot)
    X->>B: RunStarted
    X->>P: run(process, on_line, on_sample, timeout)
    loop each output line
        P-->>X: on_line(stream, text)
        X->>B: RunOutput (classified line)
    end
    P-->>X: ProcessOutcome
    X->>X: extract metrics, match failure signatures, snapshot outputs
    X->>DB: save(run: terminal) + logs
    X->>B: RunFinished
    X-->>R: Run
    alt failed and transient and retries left
        R->>R: back off, then execute(next attempt)
    end
    R-->>C: Run
```

## Batches

`RunService.run_batch` asks the dependency graph for *generations*: layers of projects that
do not depend on each other. Each layer runs concurrently (bounded by the executor's
semaphore); a layer starts only after the previous one has finished.

```mermaid
flowchart LR
    subgraph L1["layer 1 (parallel)"]
        p1[market-data-analytics]
        p2[momentum-backtest]
        p3[monte-carlo-price-simulator]
        p9[markowitz-efficient-frontier]
    end
    subgraph L2["layer 2"]
        p6[options-pricing-...]
        p10[capstone-multi-strategy]
    end
    subgraph L3["layer 3"]
        p7[credit-risk-merton]
        p8[garch-volatility]
    end
    p3 --> p6
    p6 --> p7
    p6 --> p8
    p3 --> p7
    p2 --> p10
    p9 --> p10
```

## Cancellation and process trees

A project may start its own child processes. Killing only the process the runner started
would orphan them, so `AsyncSubprocessRunner` terminates the **whole tree** (via `psutil`)
on timeout and on cancellation, escalating from `terminate` to `kill` after a grace
period. Integration tests start a grandchild, cancel, and assert it is gone.

## Persistence

Runs and their output lines live in SQLite (`SqliteRunRepository`). The schema is changed
only through the ordered list in `infrastructure/migrations.py`; each migration runs in the
same transaction as the version bump. Timestamps are stored as fixed-width UTC ISO-8601
strings, so lexicographic order is chronological order. Metrics, the configuration
snapshot and the output manifest are stored as JSON on the run row.

## Data shipped with the package

`src/quant_workbench/data/` holds the registry of portfolio manifests, the failure-signature
knowledge base (TOML) and, later, the project templates. They are package data, resolved
with `importlib.resources`, so an installed wheel and an editable checkout behave alike.
