from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import pytest

from quant_workbench.domain.events import DomainEvent
from quant_workbench.infrastructure.event_bus import InProcessEventBus


@dataclass(frozen=True, slots=True)
class Ping(DomainEvent):
    n: int = 0


@dataclass(frozen=True, slots=True)
class SpecialPing(Ping):
    pass


def test_handlers_receive_only_matching_events() -> None:
    bus = InProcessEventBus()
    seen: list[int] = []
    bus.subscribe(SpecialPing, lambda e: seen.append(e.n))

    bus.publish(Ping(n=1))
    bus.publish(SpecialPing(n=2))

    assert seen == [2]


def test_base_type_subscription_receives_subclasses() -> None:
    bus = InProcessEventBus()
    seen: list[str] = []
    bus.subscribe(DomainEvent, lambda e: seen.append(type(e).__name__))

    bus.publish(Ping())
    bus.publish(SpecialPing())

    assert seen == ["Ping", "SpecialPing"]


def test_unsubscribe_stops_delivery_and_is_idempotent() -> None:
    bus = InProcessEventBus()
    seen: list[int] = []
    cancel = bus.subscribe(Ping, lambda e: seen.append(e.n))

    bus.publish(Ping(n=1))
    cancel()
    cancel()
    bus.publish(Ping(n=2))

    assert seen == [1]


def test_a_failing_handler_does_not_block_the_others_or_the_publisher(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = InProcessEventBus()
    seen: list[int] = []

    def broken(_: Ping) -> None:
        raise RuntimeError("observer bug")

    bus.subscribe(Ping, broken)
    bus.subscribe(Ping, lambda e: seen.append(e.n))

    with caplog.at_level(logging.ERROR):
        bus.publish(Ping(n=7))

    assert seen == [7]
    assert "observer bug" in caplog.text


def test_handlers_can_publish_and_unsubscribe_reentrantly() -> None:
    bus = InProcessEventBus()
    seen: list[str] = []
    cancel_holder: list[object] = []

    def once(event: Ping) -> None:
        seen.append(f"once:{event.n}")
        cancel_holder[0]()  # type: ignore[operator]
        bus.publish(SpecialPing(n=99))

    cancel_holder.append(bus.subscribe(Ping, once))
    bus.subscribe(SpecialPing, lambda e: seen.append(f"special:{e.n}"))

    bus.publish(Ping(n=1))
    bus.publish(Ping(n=2))

    assert seen == ["once:1", "special:99"]


def test_concurrent_publishers_lose_no_events() -> None:
    bus = InProcessEventBus()
    counter_lock = threading.Lock()
    total = 0

    def count(_: Ping) -> None:
        nonlocal total
        with counter_lock:
            total += 1

    bus.subscribe(Ping, count)
    threads = [
        threading.Thread(target=lambda: [bus.publish(Ping()) for _ in range(200)]) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert total == 1600
