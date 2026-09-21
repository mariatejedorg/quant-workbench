"""The thread boundary: who runs where, and what happens when a job fails or is cancelled."""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest
from PySide6.QtCore import QObject

from quant_workbench.domain.events import DomainEvent
from quant_workbench.infrastructure.event_bus import InProcessEventBus
from quant_workbench.ui.async_bridge import AsyncBridge, EventRelay

pytestmark = pytest.mark.gui


@pytest.fixture
def bridge(qtbot) -> AsyncBridge:  # type: ignore[no-untyped-def]
    made = AsyncBridge()
    yield made  # type: ignore[misc]
    made.stop()


def test_the_coroutine_runs_on_the_engine_thread_and_the_callback_on_the_gui_thread(
    qtbot, bridge: AsyncBridge
) -> None:
    seen: dict[str, int | None] = {}

    async def work() -> int:
        seen["engine"] = threading.get_ident()
        return 42

    results: list[int] = []

    def on_result(value: int) -> None:
        seen["callback"] = threading.get_ident()
        results.append(value)

    bridge.submit(work(), on_result=on_result)
    qtbot.waitUntil(lambda: bool(results), timeout=5000)

    assert results == [42]
    assert seen["engine"] == bridge.engine_thread_id
    assert seen["engine"] != threading.get_ident()
    assert seen["callback"] == threading.get_ident()  # the GUI (test) thread


def test_an_error_reaches_on_error_on_the_gui_thread(qtbot, bridge: AsyncBridge) -> None:
    errors: list[tuple[BaseException, int]] = []

    async def fail() -> None:
        raise ValueError("boom")

    bridge.submit(fail(), on_error=lambda e: errors.append((e, threading.get_ident())))
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)

    assert isinstance(errors[0][0], ValueError)
    assert errors[0][1] == threading.get_ident()


def test_an_error_without_a_handler_is_logged_not_raised(
    qtbot, bridge: AsyncBridge, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail() -> None:
        raise RuntimeError("nobody listens")

    future = bridge.submit(fail())
    qtbot.waitUntil(future.done, timeout=5000)
    qtbot.waitUntil(lambda: "engine job failed" in caplog.text, timeout=5000)


def test_a_cancelled_job_calls_neither_callback(qtbot, bridge: AsyncBridge) -> None:
    called: list[str] = []
    started = threading.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(60)

    future = bridge.submit(
        forever(),
        on_result=lambda _: called.append("result"),
        on_error=lambda _: called.append("err"),
    )
    assert started.wait(5)
    future.cancel()
    qtbot.waitUntil(future.done, timeout=5000)
    qtbot.wait(100)

    assert called == []


def test_the_windows_event_loop_of_the_worker_can_run_subprocesses(
    qtbot, bridge: AsyncBridge
) -> None:
    """Regression guard for the design choice of ADR 0002 (Proactor loop off the main thread)."""
    outputs: list[str] = []

    async def spawn() -> str:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print('from a child')", stdout=asyncio.subprocess.PIPE
        )
        out, _ = await process.communicate()
        return out.decode().strip()

    bridge.submit(spawn(), on_result=outputs.append)
    qtbot.waitUntil(lambda: bool(outputs), timeout=15000)

    assert outputs == ["from a child"]


def test_call_runs_a_function_on_the_engine_thread(qtbot, bridge: AsyncBridge) -> None:
    where: list[int] = []

    bridge.call(lambda: where.append(threading.get_ident()))
    qtbot.waitUntil(lambda: bool(where), timeout=5000)

    assert where == [bridge.engine_thread_id]


def test_stop_cancels_running_work_and_is_idempotent(qtbot) -> None:  # type: ignore[no-untyped-def]
    bridge = AsyncBridge()
    cancelled = threading.Event()

    async def hang() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    bridge.submit(hang())
    time.sleep(0.2)

    bridge.stop()
    bridge.stop()

    assert cancelled.is_set()
    assert not bridge.is_running


# ------------------------------------------------------------------------- relay
class Ping(DomainEvent):
    pass


def test_events_from_another_thread_arrive_batched_on_the_gui_thread(qtbot) -> None:  # type: ignore[no-untyped-def]
    bus = InProcessEventBus()
    relay = EventRelay(bus, interval_ms=20)
    batches: list[list[object]] = []
    threads: list[int] = []

    def receive(batch: list[object]) -> None:
        batches.append(batch)
        threads.append(threading.get_ident())

    relay.batch.connect(receive)

    def publish_many() -> None:
        for _ in range(500):
            bus.publish(Ping())

    worker = threading.Thread(target=publish_many)
    worker.start()
    worker.join()
    qtbot.waitUntil(lambda: sum(len(b) for b in batches) == 500, timeout=5000)

    assert set(threads) == {threading.get_ident()}
    assert len(batches) < 500  # batched, not one signal per event
    relay.close()


def test_closing_the_relay_flushes_and_unsubscribes(qtbot) -> None:  # type: ignore[no-untyped-def]
    bus = InProcessEventBus()
    relay = EventRelay(bus, interval_ms=10_000)  # the timer will not fire on its own
    batches: list[list[object]] = []
    relay.batch.connect(batches.append)

    bus.publish(Ping())
    relay.close()
    bus.publish(Ping())  # after close: nobody is listening
    relay.drain()

    assert sum(len(b) for b in batches) == 1


def test_the_bridge_and_relay_are_qobjects_with_the_expected_lifetime(qtbot) -> None:  # type: ignore[no-untyped-def]
    parent = QObject()
    bridge = AsyncBridge(parent)
    try:
        assert bridge.parent() is parent
    finally:
        bridge.stop()
