# 0002. asyncio engine in a worker thread, bridged to Qt signals

- Status: Accepted
- Date: 2026-09-21

## Context

Running a project means spawning a subprocess, streaming its output line by line,
sampling its CPU/RAM, enforcing a timeout, and being able to kill its **whole process
tree** on cancel, for several projects concurrently, without ever freezing the UI.

## Decision

- The engine is written against `asyncio` (`asyncio.create_subprocess_exec`, tasks,
  semaphores, `asyncio.timeout`). It has no knowledge of Qt.
- The desktop app runs the engine's event loop in a **dedicated worker thread**. A small
  `AsyncBridge` (in `ui/`) is the only async/Qt frontier: it submits coroutines with
  `asyncio.run_coroutine_threadsafe` and re-emits domain events as Qt signals, which Qt
  delivers to the GUI thread through queued connections.
- The CLI simply calls `asyncio.run(...)` on the same use cases.
- `InProcessEventBus` is synchronous and thread-agnostic; it is the seam between the
  engine and whoever observes it.

## Consequences

- One implementation of "run a project" serves the GUI, the CLI, the scheduler and the
  tests; behaviour cannot diverge between them.
- On Windows the default `ProactorEventLoop` supports subprocesses in non-main threads;
  a smoke test in the M2 suite pins this down.
- Cancellation kills the process tree with `psutil` so no orphaned `python.exe` remains.
- Cost: a thread boundary to reason about. It is confined to one small module, which is
  covered by tests that assert no callback ever runs on the wrong thread.

## Alternatives considered

- **`QProcess` for runs** — idiomatic Qt and simple, but couples the engine to Qt, which
  would make the CLI impossible to share and the engine untestable without a display.
- **`qasync` (Qt event loop driving asyncio)** — attractive, but one blocked callback
  then stalls both worlds, and it is one more dependency on a niche project.
- **`concurrent.futures` thread pool + blocking `subprocess`** — workable, but streaming
  output from many processes needs a reader thread per stream; asyncio expresses the same
  thing as data flow rather than as thread management.
