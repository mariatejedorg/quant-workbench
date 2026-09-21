from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from quant_workbench.infrastructure.logging_setup import configure_logging, log_context


def _records(log_dir: Path) -> list[dict[str, object]]:
    lines = (log_dir / "workbench.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_file_output_is_json_lines_with_context(accented_root: Path) -> None:
    configure_logging(accented_root, console=False)
    log = logging.getLogger("quant_workbench.test")

    with log_context(project="credit-risk-merton-model", run_id="abc"):
        log.info("started", extra={"attempt": 2})
    log.warning("outside any context")

    first, second = _records(accented_root)
    assert first["message"] == "started"
    assert first["project"] == "credit-risk-merton-model"
    assert first["run_id"] == "abc"
    assert first["attempt"] == 2
    assert "project" not in second
    assert second["level"] == "WARNING"


def test_reconfiguring_does_not_duplicate_handlers(accented_root: Path) -> None:
    for _ in range(3):
        configure_logging(accented_root, console=False)
    logging.getLogger("quant_workbench.dup").info("once")

    assert len(_records(accented_root)) == 1


def test_unicode_survives_the_round_trip(accented_root: Path) -> None:
    configure_logging(accented_root, console=False)
    logging.getLogger("quant_workbench.utf8").info("Proyecto María — volatilidad")

    assert _records(accented_root)[0]["message"] == "Proyecto María — volatilidad"


async def test_context_does_not_leak_between_concurrent_tasks(accented_root: Path) -> None:
    configure_logging(accented_root, console=False)
    log = logging.getLogger("quant_workbench.tasks")

    async def work(name: str) -> None:
        with log_context(project=name):
            await asyncio.sleep(0.01)
            log.info("working")

    await asyncio.gather(work("alpha"), work("beta"), work("gamma"))

    assert sorted(str(r["project"]) for r in _records(accented_root)) == ["alpha", "beta", "gamma"]
